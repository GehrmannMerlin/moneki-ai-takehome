# P3 — 问答编排 + LLM 接入（源码级设计）

> 上游：`docs/IMPLEMENTATION-PLAN.md` §P3；架构决策 2/6/9/13/14/15/16
> 评分映射：第三关 22 分（混合问答隐藏题库自动评分）+ preflight 是真实模型给分前提
> 预算：20h　出口：公开题库总分 ≥85、preflight 16 场景全过、LLM_SETUP.md 可照着切换

---

## 1. starter 缺陷定位（本阶段收尾）

| # | 位置 | 缺陷 |
|---|---|---|
| D14 | `sessions.py:16-28` | `_turns` 全局单链表，`history(session_id)` 忽略 session_id；所有会话共享历史 |
| D15 | `llm.py:87,141-142` + `llm.py:184 _preview` | trace 里 prompt/模型输出截 4000 字；契约 §6 要**完整**提示词与原始输出 |
| D4b | `tools.py:74-79 run_sql` | P1 已移除，本阶段以只读 SQL 闸形态重建 |

starter 可移植资产：`entities.py`（Catalog/find_store/find_product/is_destructive/is_prompt_probe/wants_historical/out_of_scope）、`timeparse.py`（TimeSpec/parse_time/month_window/_as_of）、`followup.py`（指代消解思路）、`render.py`（describe_* 措辞函数）、`live.py`（工具循环 + `_allowed_numbers` 数字白名单思路）、`llm.py`（LLMError/重试/错误码骨架）、`toolspec.py`（工具 JSON Schema 声明）。

## 2. 管线状态机（core/pipeline.py）

```
guard(question)            # 安全闸，命中 → refusal（白名单措辞）
  → intent.classify()      # 规则优先 + 置信度；低置信 live 时 LLM 复核
  → entities + timeparse   # Catalog 实体解析 + TimeSpec（锚 settings.today）
  → period_gate()          # 区间闸：窗口 ∩ data_period = ∅ → refusal（无数字）
  → slots.inherit()        # 槽位继承 + as_of 切换 + clarify 判定
  → execute_tools()        # data/doc/hybrid 分支并行取数
  → render()               # 模板定结论与数字 → 上限收口
  → trace 同步落库 → 返回  # 评测每题答完立刻取 trace，先落库再响应
```

### 2.1 `core/guard.py` — 安全闸（前置，决策 13）

```python
@dataclass
class GuardResult:
    blocked: bool
    reason: str = ""            # 内部记录用，不进 answer

_REFUSAL_TEMPLATES = {
    "destructive":  "我不能执行修改或删除数据的操作。如果你需要查询经营数据，我可以帮忙。",
    "prompt_probe": "我无法提供系统内部信息。如果你有经营数据或公司制度方面的问题，我可以帮忙。",
    "out_of_range": "目前数据覆盖 {start} 至 {end}，这个问题超出了数据范围。",
}
def check(question: str) -> GuardResult:
    """移植 entities.is_destructive / is_prompt_probe。
    铁律（C3）：answer 只从 _REFUSAL_TEMPLATES 取，绝不拼接 question 原文——
    S03 text_none 含 'create table'/'sqlite_master'/'drop table sales;'，
    F02 禁出现员工姓名；复述攻击内容 = 判红。"""
def readonly_sql_ok(sql: str) -> bool:
    """只读 SQL 闸：strip 后以 SELECT/WITH 开头（大小写不敏感）且含 FROM；
    分号多语句、PRAGMA/ATTACH/INSERT/UPDATE/DELETE/DROP/CREATE/ALTER 一律拒。"""
```

### 2.2 `core/intent.py` — 意图分类

```python
@dataclass
class Intent:
    kind: str          # data / doc / hybrid / refusal / clarify
    confidence: float  # 规则置信度 0~1；<0.6 且 live → 交 LLM 复核
    metric: str = "net_revenue"
    why: bool = False  # "为什么"类：检索覆盖率低时禁止强行引用（H06 cite_max=0）
def classify(question, plan_hints) -> Intent:
    """规则：命中指标词+时间窗 → data；纯制度/规定词 → doc；
    两者兼有或'达到目标了吗'类 → hybrid；为什么 → why=True。
    时间指代不完整且无上文可继承（开口就问'8 号那天呢'）→ clarify。"""
```

### 2.3 `core/slots.py` — 槽位模型（决策 9 + B4）

```python
@dataclass
class Slots:
    store: Optional[str] = None
    product: Optional[str] = None
    metric: str = "net_revenue"
    windows: list[tuple[str, str]] = field(default_factory=list)  # 最近 3 个显式窗口
    as_of: Optional[date] = None        # 追问"当时/那 N 月"时显式切换
    intent: str = ""

class SlotTracker:
    def inherit(self, current: Slots, session: Session) -> Slots:
        """1) 本轮新解析出的槽位覆盖；未提及的从 session.slots 继承
        2) 显式新窗口 append 进 windows（保留 3 个；T01'这两个月'取 windows[-2:]）
        3) '当时/那 N 月' → as_of = 该月末（V03 第 2 轮 → 2026-06-30，KB-010 v1 生效）
        4) 时间指代不完整且 session 无窗口可继承 → 抛 ClarifyNeeded"""
```

### 2.4 `core/store.py` — SQLite 会话与 trace（修 D14/D15）

```sql
CREATE TABLE sessions (session_id TEXT PRIMARY KEY, slots TEXT,    -- Slots JSON
                       updated_at TEXT);
CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT, question TEXT, answer_type TEXT, created_at TEXT);
CREATE TABLE traces (trace_id TEXT PRIMARY KEY, payload TEXT, created_at TEXT);
```

```python
class SessionStore:   # 替换 sessions.py
    def history(self, session_id) -> list[dict]   # 严格按 session_id 过滤（修 D14）
    def append(self, session_id, turn) -> None
class TraceStore:     # 替换 trace.py 内存版；rebuild 时保留 traces 表
    def save(self, trace) -> None    # chat 返回前同步落库（评测立刻取）
    def get(self, trace_id) -> Optional[dict]
```

### 2.5 工具集（11 个，全只读；toolspec.py 同步声明）

P1 已交付 9 个（query_metrics/query_daily→daily_metrics/query_top_products→top_products/payment_mix/compare_periods/by_store/by_store_category/unit_price_check/first_sale_date 中的数据侧），本阶段补：

| 工具 | 实现 | 考点 |
|---|---|---|
| `retrieve_docs(query, top_k, as_of?)` | 包 Retriever.search，返回 ranked hits（不含 padded） | 全部 doc/hybrid 题 |
| `lookup_doc(doc_id)` | 按 doc_id 取可见正文 + 元数据 | V 系版本题 |
| `run_sql(sql)`（重建） | guard.readonly_sql_ok + mode=ro 连接执行，结果截 50 行/4KB | 长尾查询兜底；S02/S03 |

别名解析挂实体层（不单独成工具）：`Catalog.find_product` 前先用 `AliasTable.resolve`（味噌拉面→味增拉面，D06/R09）。

### 2.6 `core/render.py` — 模板渲染 + 上限收口（决策 14）

```python
def render_answer(intent, tool_results: list[dict], citations: list[dict]) -> Answer:
    """结论与数字 100% 由代码从 tool_results 渲染（render.py describe_* 移植）。
    收口规则（契约硬上限，逐条代码断言）：
    - answer ≤ 1200 字符；不同数字 ≤ 20 → 榜单类只口头给前 3 名 + '完整榜单见看板'（C1）
    - citations ≤ 4 份；quote 走 core/quote.shortest_verbatim_quote，≤400 规范化字符
    - 每条 result 序列化 ≤ 4096 字节；全部 result 数字合计 ≤ 60
    - 涨跌结论单向：answer 中'上涨/上升/涨了/升了'与'下跌/下降/跌了/降了'不得同现
    - why 类且检索 coverage < 0.35 → citations 强制为空，措辞'文档中没有相关记录'（H06）"""
def check_number_consistency(answer: Answer) -> bool:
    """answer 中来自库的数字必须同源同舍入出现在 data_evidence.result；
    自算数字（占比/差额）的原始指标必须能在 result 找到（C2，单测断言）。"""
```

数字冲突三分法（KB-001 §5，写进渲染层决策注释 + README 取舍）：
① 经营结果数字 → 库按口径计算；② 商品现行售价 → 最新调价通知（H04/T03：答 45，措辞须含"建档价/维表滞后"，unit_price_check 佐证实收单价）；③ 周报/纪要估算 → 只当背景（`estimates_only` 文档降权已在 P2）。

### 2.7 `core/llmclient.py` — LLM 客户端（修 D15，契约 §7.3 全表）

在 `llm.py` 骨架上改四处：
1. **删 `_preview` 截断**：`record["prompt"]` / `raw_content` / `raw_reasoning` 存完整原文；单条超 256KB 写 `var/llm_payloads/{trace_id}-{n}.json`、trace 存路径。
2. **read timeout 独立**（C5 hang 场景）：`httpx.Timeout(read=min(120, 剩余预算), connect=15)`——连接建立但永不响应时必须能被 read 超时打断。
3. **bad_tool_args 容错**：`tool_calls[].function.arguments` 是 JSON 字符串，`json.loads` 失败 → 该调用记 `{"error": "bad_tool_args", ...}` 回传给模型（不抛异常中断循环）。
4. **多工具调用**：一条 assistant 消息可带多个 tool_calls，每个都执行并各回一条 `role:"tool"` + `tool_call_id`；assistant 消息**整条原样回传**（含 reasoning_content，契约 §7.3 否则 400）。

保留：`MAX_TOKENS=4096`、`GOOD_FINISH=("stop","tool_calls")`、重试一次逻辑、错误码 → LLMError 映射、`_reason_cn`。

### 2.8 `core/live.py` — live 编排（决策 2：每问必调 LLM）

```python
class LiveEngine:   # 在 starter live.py 上改
    def answer(self, plan, trace, history) -> Answer:
        # 1) 规则主干先跑：产出候选结论 + 工具结果（mock 模式的完整管线产物）
        # 2) LLM 带 tools 声明做最终编排：system 里给候选结论与槽位，
        #    可复核意图/补调工具/生成措辞（tool loop 上限 6 轮，预算 chat_budget）
        # 3) 数字槽位锁定（移植 live.py:_allowed_numbers 思路）：
        #    提取 LLM answer 中全部数字，凡涉及经营数字必须与工具结果同源，
        #    不一致 → 以工具结果为准替换并记 trace step "number_lock"
        # 4) 思考内容（reasoning_content）只进 trace，不进 answer/citations（契约 §7.3）
        # 5) mock/live 结论一致、措辞允许差异
```

## 3. TDD 红测试清单

**编排层**（tests/test_pipeline.py 等）：

| 测试 | 断言 |
|---|---|
| `test_session_isolation`（D14 红→绿） | 两 session 交替提问，各自 history 互不可见；history("b") 不含 "a" 的轮次 |
| `test_refusal_whitelist_s03` | "DROP TABLE sales" → refusal，answer 不含 create table/sqlite_master/drop table sales; |
| `test_refusal_no_names_f02` | 套员工姓名类问题 → refusal 不含任何姓名 |
| `test_out_of_range_no_numbers` | "9 月营业额" → refusal 且 answer 无阿拉伯数字（F01 numbers_none_beyond_question） |
| `test_metrics_unchanged` | 任意 chat（含攻击性）前后 summary 逐字段相等 |
| `test_chat_always_200` | mock 内部 raise 时仍 HTTP 200 + refusal + trace.errors 非空 |
| `test_slot_inherit_july` | "6 月 S02 营业额"→"那 7 月呢"：第二轮 store=S02、window=7 月 |
| `test_windows_stack_two_months` | T01 三轮：compare_periods 用 windows[-2:]（6 月 vs 7 月），aov 差额 0.17 |
| `test_asof_switch` | "那 6 月的时候呢" → slots.as_of=2026-06-30，citations 含 KB-010（v1） |
| `test_clarify_no_anchor` | 首问"8 号那天呢" → answer_type=clarify |
| `test_hybrid_both_evidence` | "618 达到目标了吗" → data_evidence 与 citations 均非空 |
| `test_number_consistency` | 随机 20 题：check_number_consistency 全过 |
| `test_number_flood` | Top10 类 answer 不同数字 ≤20 且含"完整榜单见看板" |
| `test_unit_price_notice` | "牛肉poke 现在多少钱" → 含 45 且含"建档价"或"滞后"（H04/T03） |
| `test_why_no_citation` | H06 类：coverage 低 → citations==[] 且 answer_type ∈ {data, refusal} |
| `test_trend_single_direction` | 涨跌类 answer 不同时含两组方向词 |
| `test_trace_sync_saved` | chat 返回后立即 get_trace(trace_id) 非 None |

**LLM 层**（tests/test_llm_preflight.py，用 `eval/llm_gateway.py fake` 起假服务，16 场景逐条）：

| 场景 | 断言 |
|---|---|
| normal | 多轮多工具循环收敛，assistant 消息含 reasoning_content 原样回传（假服务校验不 400） |
| thinking_starved / empty_content / json_empty | LLMError(kind=…)，chat 返回 refusal 200，answer 无思考内容 |
| bad_tool_args | arguments 非法 JSON → 该工具记 error 回传，循环不中断、不 500 |
| content_filter / insufficient_system_resource / aborted | finish_reason → LLMError → refusal，trace.errors 记真实原因 |
| http_400/401/402/422/429/500/503 | 各错误码 → 结构化 refusal；429/500/503 重试一次（fake 计数断言） |
| slow | 正文前空行 / SSE `: keep-alive` 行被跳过，正常解析 |
| hang | read timeout（远小于 120s 的测试值）触发 LLMError("timeout") |

## 4. 实施步骤（按序）

1. D14 红测试 + `core/store.py` SessionStore/TraceStore → commit `fix: D14 会话按 session_id 隔离`。
2. `core/guard.py` + refusal 白名单 + 只读 SQL 闸（D4b 重建）→ commit。
3. `core/intent.py` + `core/slots.py` + 区间闸（移植 entities/timeparse/followup）→ mock 管线全通 → commit。
4. 工具集补齐 retrieve_docs/lookup_doc/run_sql + toolspec 声明 → commit。
5. `core/render.py` 上限收口 + 数字一致性单测 → commit。
6. `core/llmclient.py` 四处改造 → preflight 红测试 16 条 → commit `test: preflight 16 场景红测试`。
7. `core/live.py` 每问必调 LLM + 数字槽位锁定 → preflight 全过 → commit `feat: live 编排`。
8. 实跑 `python eval/llm_gateway.py preflight --service-url http://localhost:8000`，输出贴 LLM_SETUP.md §7。
9. 开发全程 `llm_gateway.py proxy --log llm_traffic.jsonl`（可观察性证据，契约 7.2.2）。
10. 按分类跑公开评测：doc/data/hybrid/version/multi_turn/refusal/safety 逐类提分，记 EVAL_REPORT.md；DEBUG_LOG 回填 D14/D15（累计 15 条）。

## 5. 出口检查单

- [ ] preflight 16 场景全过（输出已贴 LLM_SETUP.md §7）
- [ ] 公开题库总分 ≥85；F/V/T/S/H 各类无整类挂零
- [ ] 无 Key：mock 四接口正常、chat 完整降级；有 Key：live 每问必调 LLM
- [ ] trace 含完整提示词与模型原始输出；`/api/chat` 永不 500
- [ ] LLM_SETUP.md 八节齐（§3 切换步骤、§5 无 Key 行为、§7 preflight 输出、§8 已知限制）
