# Moneki.ai 实操作业 — 分阶段实施规划（TDD/SDD 工程化）

> 状态：v1（2026-09-25）
> 上游输入：`README.md`（作业要求与评分表）、`docs/API_CONTRACT.md`（强制契约）、`eval/`（评分脚本 + 55 题公开题库）、`docs/ARCHITECTURE.md`（17 项架构决策）、`docs/ARCHITECTURE-REVIEW.md`（审查修订 + 缺陷清单 15 条）
> 方法论：**SDD（规范先行）框住每一阶段，TDD（红→绿）驱动每一模块，评测即回归贯穿全程。**
> **源码级分阶段设计（starter 全源码对照，缺陷定位到行号）：[`docs/phases/`](phases/)**
> - [P0 — 环境与基线](phases/P0-环境与基线.md)
> - [P1 — 数据层（清洗 + 口径引擎 + metrics API）](phases/P1-数据层.md)
> - [P2 — 检索层（Loader/切块/BM25+向量 RRF/版本过滤）](phases/P2-检索层.md)
> - [P3 — 问答编排 + LLM 接入](phases/P3-问答编排与LLM.md)
> - [P4 — 前端看板 + 对话栏 + 调试面板](phases/P4-前端与调试面板.md)
> - [P5 — 收尾验收](phases/P5-收尾验收.md)

---

## 0. 总纲：三层工程化纪律

本计划把 72 小时的作业拆成 6 个阶段（P0–P5），每个阶段遵循同一条纪律链：

```
SDD 规范层：本阶段交付物的"契约/规约"先写死（接口形状、口径定义、验收阈值）
   ↓
TDD 测试层：把规约翻译成可执行的红测试（fixture 写死合法，逻辑写死违法）
   ↓
实现层：最小实现让红转绿，禁止超过测试断言范围的"顺手发挥"
   ↓
回归层：每阶段结束跑公开评测（run_eval.py），分数 + commit 记入 EVAL_REPORT.md
```

三条全局铁律（贯穿所有阶段）：

1. **红先行**：任何修复/新功能，先提交一个"在现状上是红的"测试，再提交实现。第二关尤其如此——评分表明确看"先加一个会红的测试，再修"的提交顺序。
2. **评测即回归**：`run_eval.py` 是唯一的裁判。每个阶段出口都以"跑一遍公开题库、分数对比基线分类分解表"为准，不以"我觉得写完了"为准。
3. **零写死**：评委第 3 步会换 `data/` 和 `knowledge_base/` 重建后跑隐藏题库。任何数字、答案、文档内容不得写进代码逻辑；期望值只能出现在测试 fixture 里。

### 评分映射总表（阶段 → 分数）

| 阶段 | 交付 | 对应评分维度 | 分值 |
|---|---|---|---|
| P0 | 环境可复现 + 基线分解 + 过程文件骨架 | Git 过程 + AI_USAGE + 可复现性 | 8（过程分全程累积） |
| P1 | 清洗层 + 口径引擎 + metrics API | 第一关 | 12 |
| P2 | 混合检索层 + starter 缺陷修复留证 | 第二关（检索质量 + DEBUG_LOG） | 20 |
| P3 | 问答编排 + LLM 接入 + preflight | 第三关（混合问答） | 22 |
| P4 | 前端看板 + 对话栏 + 调试面板 | 第四关（可调试性与回归） | 8 |
| P5 | 必交文件 + 换库自验 + 模拟验收 | 可复现性兜底 | （贯穿分） |

72 小时建议预算：P0≈3h，P1≈10h，P2≈16h，P3≈20h，P4≈12h，P5≈8h，缓冲≈3h。P2/P3 是核心分仓（42/70），时间过半时若 P3 未启动，砍 P4 加分项保 P3。

---

## 阶段 P0：环境可复现 + 基线测绘（出口：干净环境 3 步跑通）

### 0.1 SDD 规范（本阶段交付物的验收契约）

| 项 | 规范 |
|---|---|
| 启动 | 评委在干净环境按 README 三步内起服务，无 Key 时四接口正常（健康/指标/检索可答，chat 走 mock 降级） |
| 重建 | 一条命令从 `data/` + `knowledge_base/` 生成全部产物；不依赖本机绝对路径；Windows/POSIX 均可执行 |
| 基线 | starter 原始得分 19.50/100 已存档（report.json），且按 category 分解成分类表，贴入 EVAL_REPORT.md 开头 |
| 过程文件 | AI_USAGE.md / DEBUG_LOG.md / EVAL_REPORT.md / LLM_SETUP.md 骨架入库，从本阶段起随手记 |

### 0.2 实施步骤（按序）

1. **Windows 兼容重建链**：本机无 make 且 starter Makefile 用 POSIX 路径——重写 Makefile（自动识别 `.venv/Scripts/` 与 `.venv/bin/`），README 同时附"直接命令表"（不依赖 make）。重建命令统一为 `python -m kbqa.rebuild`。
2. **删除定时炸弹**：删除 starter 已提交的 `starter/.cache/index.json`（缓存键不含知识库内容，评委换库即炸），`.cache/` 与 `var/` 入 `.gitignore`。
3. **跑基线**：用 starter 原样跑公开题库，存 `eval/baseline_report.json`；写一个小脚本按 category（normal/retrieval/data/doc/hybrid/version/multi_turn/followup/clarify/refusal/safety）分解得分，生成分类基线表。
4. **建过程文件骨架**：四份必交文档建空骨架入库；AI_USAGE.md 从本阶段起"每次被 AI 坑了立刻记一条"（最后一天补不出来）。
5. **测试基建**：`pytest` 配置 + `conftest.py`（fixture：临时 var 目录、fake LLM 服务、评测期望值常量表），`eval/tests` 自测 29 条并入 CI 式回归脚本。

### 0.3 TDD 红测试清单（P0）

| 测试 | 断言 | 红的原因 |
|---|---|---|
| `test_rebuild_idempotent` | 连续两次 rebuild，产物行数一致、索引可检索 | starter 缓存键缺陷导致第二次读旧缓存 |
| `test_no_absolute_path` | grep 源码与产物，无 `D:\`/`/home/` 类绝对路径 | （预防性，写死即红） |

### 0.4 出口标准

- [ ] 干净 venv 三步跑通（装依赖 → rebuild → 起服务）
- [ ] 基线分类分解表入库（EVAL_REPORT.md §0）
- [ ] 四份过程文档骨架入库，commit 历史从本阶段开始分次提交

---

## 阶段 P1：清洗层 + 口径引擎 + metrics API（第一关，12 分）

### 1.1 SDD 规范（口径即契约，先于代码冻结）

**输入**：`data/pos.db` 三表（sales 18628 行，qty/amount 为 TEXT）。
**输出**：`var/clean.db` 的 `clean_lines` 表（规范化全量行 + 行级双口径剔除标记）。

规范化四条（写进代码 docstring 即规范）：
- 编号 trim + upper；日期支持三种格式且**必须过 `datetime.date` 构造校验**（`'2026-13-45'` 格式合法但日历非法，按规则 1 剔除——只靠正则会多留 3 行，N01 必红）；amount 去 `¥`/`￥` 与空白；qty 按整数。

剔除六规则（KB-001 v3 §3，按顺序首因归因）：

| 规则 | 剔除数（已独立复算验证） |
|---|---|
| 1 日期无法解析/日历非法 | 8 |
| 2 amount 为空（不回填） | 150 |
| 3 qty ≤ 0 | 30 |
| 4 脏门店外键 | 10 |
| 5 脏商品外键 | 40 |
| 6 七字段完全重复 | 100 |
| **合计剔除 / 保留** | **338 / 18290**（18196 销售行 + 94 退款行） |

指标五件套（KB-001 v3 §4）：净营业额**含退款**；有效订单数=销售行 `DISTINCT order_id`；客单价=净额/有效订单数，`ROUND_HALF_UP` 两位；销量=销售 qty−退款 qty；退款按自身日期归属；区间**闭区间**。口径引擎参数化（v3 默认 / v2 可选），v2 差异三处：退款剔除、空 amount 回填 qty×unit_price、客单价分母为明细行数。

API 行为契约：`/api/metrics/summary` 与 `/api/metrics/daily` 对空区间返回 0（`aov` 为 `null`），daily 区间内每天必有一条记录。`/api/health` 的 `valid_sales_rows` 取清洗结果，`kb_docs` 取**入索引文档数**（35，非目录文件数 36）。

### 1.2 TDD 红测试清单（P1）

fixture 用公开题库期望值（写死合法）：N01（18290/35）+ M01–M06 全字段期望值。

| 测试 | 断言 | 对应考点 |
|---|---|---|
| `test_clean_total_conservation` | 保留 + 剔除 = 18628；六项剔除计数 = 8/150/30/10/40/100 | N01 |
| `test_valid_sales_rows_18290` | `/api/health` 返回 18290 | N01 |
| `test_calendar_illegal_date_rejected` | `'2026-13-45'` 行进 rejects（reason=日期） | 隐藏陷阱 |
| `test_summary_m01_june` | 6 月五指标 = 156757.00/953.00/4311/36.36/6496 | M01 |
| `test_summary_m02_s02_july` | 7 月 S02 = 41740.00/107.00/875/47.70/1395 | M02 |
| `test_summary_m04_618_s02_p06` | = 3625.00/0/53/68.40/125 | M04 |
| `test_closed_interval` | start=end=某日 时该日数据计入（starter 右开区间缺陷红） | D 系题 |
| `test_empty_range_returns_zero` | 空区间 0/null 不报错 | M05 |
| `test_daily_pads_missing_days` | 区间内无营业额的日期也出现，值为 0 | 契约 §3 |
| `test_v2_v3_divergence` | 同一查询 v2/v3 结果不同且各符合各自口径 | 版本题地基 |
| `test_aov_rounding_half_up` | 客单价舍入为 HALF_UP（compare_periods 差额 0.17=36.53−36.36 的前提） | D04/T01 |

### 1.3 实施步骤

1. 对 starter `cleaning.py` 写缺陷复现测试（#1 不清洗、#2 右开区间、#3 v2/v3 混淆）→ 提交 `test: 复现清洗层缺陷（红）`。
2. 新核心 `core/cleaning.py` + `core/metrics.py`（口径引擎）实现 → 同一批测试转绿 → 提交 `fix: 清洗与口径引擎`。
3. metrics API 接入同一引擎（外壳复用 starter server，路由不动）。
4. 跑公开评测，M 类 + N01 应全绿；分数与分类表对比基线，记 EVAL_REPORT.md。

### 1.4 出口标准

- [ ] M01–M06 全对，N01 绿（valid_sales_rows=18290, kb_docs 待 P2 到位后为 35）
- [ ] 保留+剔除=18628 守恒断言通过
- [ ] 数据质量面板所需的"六项剔除原因分布"有查询接口产出（供 P4 前端消费）

---

## 阶段 P2：混合检索层（第二关，20 分）

本阶段与 DEBUG_LOG 深度绑定：15 条已核实缺陷（REVIEW 附录 D）逐条走**红→绿工作流**——① 对 starter 老代码写复现测试（断言正确行为→老代码上红），提交 `test: 复现缺陷 X（红）`；② 新核心实现后转绿，提交 `fix: 缺陷 X 根因+修复`；③ DEBUG_LOG 每条引这对 commit 作为"修复前确实是红的"证据。

### 2.1 SDD 规范（分五个子模块冻结）

**S2-a Loader 规约**：四种后缀（.md/.markdown/.txt/.html）；UTF-8→GB18030 降级解码（KB-062）；HTML 剥标签+实体反转义（KB-061）；frontmatter 解析 + 三级降级链（frontmatter → 文件名 KB-\d+ → 正文正则提取生效日期，status 默认"现行"，提不到当"自始有效"，元数据来源记 trace）；无 KB 编号文件跳过 + warning（kb_docs=35）。

**S2-b 可见正文管线规约（与评测脚本同构）**：直接复刻 `run_eval.py` 的 `normalize_doc`/`decode_bytes`/`html_to_text`：NFKC → 去全部空白（含零宽空格）→ 去 `*`/`` ` ``/`|`/`#`/`>`。chunk 从可见正文切、逐字保留原文；quote 由代码截取"含目标事实的最短连续句段"（≤400 规范化字符）。**绝不让 LLM 写 quote**（小模型会改全半角，逐字校验必挂）。

**S2-c 切块与索引规约**：标题层级感知，200~500 字（过短向上合并、过长按段落二切），不丢文尾（starter 缺陷 #10）；chunk_id=`doc_id#n`。索引缓存键=知识库内容哈希+代码版本。

**S2-d 检索规约**：jieba 分词 + KB-003 别名挂自定义词典；跨语言别名归一入索引（Salmon→三文鱼poke）；BM25 ∥ multilingual-MiniLM（CPU，~470MB，启动期预加载）→ RRF(k=60)；**先过滤后截取**恰好 top_k 条（不足才允许更少，补齐项标 padded 且问答链路禁用）；同一文档限占 1 格。版本过滤：as-of（默认 2026-09-01）滤"已废止"不滤"归档"；排序先验：通知/政策类 > 总表/参考类（KB-062 23:00 压 KB-042 21:30）。

**S2-e 注入清洗规约**：chunk 进 LLM 上下文前做指令句识别+剥离（移植 starter `sanitize.py` 正则），被剥句子记 trace `dropped_instructions`；检索打分与 quote 仍用原文（评测逐字校验对的是原文）。KB-060 第 42 行注入是实弹考点（S01 `numbers_none: 9999999`）。

### 2.2 TDD 红测试清单（P2）

| 测试 | 断言 | 对应缺陷/考点 |
|---|---|---|
| `test_loader_four_suffixes` | 索引文档数=35，KB-022/061/062 在列 | #5/#7 |
| `test_gbk_decoding` | KB-062 正文含"23:00"无乱码 | #8 |
| `test_html_stripped` | KB-061 chunk 无标签残留 | #9 |
| `test_chunker_keeps_tail` | 每篇文档全文被 chunks 全覆盖（拼接==正文） | #10 |
| `test_cache_key_kb_content` | 改一个知识库文件 → 缓存键变化 → 索引重建 | #11 |
| `test_retrieve_doc_id_correct` | 每个 hit 的 doc_id == 其 chunk 真实所属文档 | #12 |
| `test_retrieve_exactly_topk` | 有已废止文档被滤时 results 仍=top_k | #13 + 契约 §4 |
| `test_asof_filtering` | as_of=今天滤 KB-010 v1；as_of=2026-06-30 时 v1 有效 | V 系题 |
| `test_notice_beats_master` | "周五营业到几点" 命中 KB-062 优先于 KB-042 | C03/R03 |
| `test_crosslingual_alias` | "salmon" 查询命中三文鱼 poke 相关 chunk | KB-022 |
| `test_injection_stripped` | KB-060 注入句进 dropped_instructions，不进上下文 | S01 |
| `test_quote_verbatim_eval_compatible` | 对全部测试 citations 跑评测同款逐字校验 | 每轮必查 |
| `test_retrieve_never_500` | 向量模型不可用时降级纯 BM25，接口正常 | 契约 7.6 |

### 2.3 实施步骤

1. 按缺陷清单逐条写复现测试（可批量，但 commit 按缺陷分组）→ 红。
2. 实现 Loader + 正文管线（复刻评测函数）→ 切块器 → 索引（BM25 先行，向量后接）。
3. `/api/retrieve` 切换到新检索（与问答链路同一套实现，契约硬性要求）。
4. 接入向量与 RRF；向量下载失败自动降级。
5. 每完成一层跑公开评测观察 retrieval 类（15 题）得分爬升；DEBUG_LOG 逐条回填（现象/假设/验证/根因/修复 commit/回归测试）。
6. DEBUG_LOG 目标 ≥12 条（清单 15 条，留 3 条容错）。

### 2.4 出口标准

- [ ] retrieval 类 15 题显著超基线（目标接近满分）
- [ ] DEBUG_LOG ≥12 条，每条"现象→根因→红测试证据→修复 commit"闭环
- [ ] `kb_docs=35`，缓存随知识库内容失效
- [ ] `/api/retrieve` 恰好 top_k、先滤后取、doc_id 正确

---

## 阶段 P3：问答编排 + LLM 接入（第三关，22 分）

### 3.1 SDD 规范（管线即状态机，逐站冻结）

```
安全闸 → 意图分类（规则优先+置信度分流） → 实体/时间解析（锚 2026-09-01）
→ 区间闸 → 槽位继承 → 并行工具执行 → 模板渲染 → 上限收口 → trace 同步落库
```

**安全闸**：前置；注入/删改数据/套系统信息→直接 refusal。**refusal 措辞白名单化**：只说"不能执行修改数据的操作/超出数据范围"——不复述攻击内容、不带表名/SQL/姓名（S03 `text_none` 含 `create table`/`sqlite_master`/`drop table sales;`，F02 禁出现员工姓名）。只读 SQL 白名单（SELECT/WITH 开头且有 FROM）。

**区间闸**（chat 与 API 行为相反，这是关键区分）：解析出的时间窗与 `data_period`（2026-05-01~08-31）无交集 → refusal（如实说明数据区间，**不带任何数字**——F01 `numbers_none_beyond_question`）；metrics API 对空区间照契约返回 0。

**槽位模型**：`{store, product, metric, windows[], as_of, intent}`；`windows[]` 保留最近 3 个显式时间窗（T01"这两个月"取最近两个做 compare_periods）；追问"当时/那 N 月"显式切换 as_of（V03 第 2 轮切到 2026-06-30 使已废止的 KB-010 v1 生效）；时间指代不完整且无上文可继承 → clarify 反问。

**工具集（11 个，全只读，共用口径引擎）**：query_metrics / query_daily / query_top_products / retrieve_docs / lookup_doc / payment_mix / compare_periods（差额=**舍入后**指标之差，方向单向）/ by_store / by_store_category / unit_price_check（按门店拆）/ first_sale_date。starter `tools.py` 有全部雏形，移植接口形状、只修口径。

**数字冲突三分法**（KB-001 §5）：① 经营结果数字以库按口径计算为准；② **商品现行售价以最新调价通知为准**（H04/T03 期望 45 而非建档价 42，且须提及"建档价滞后"）；③ 周报/纪要人工估算只当背景。

**渲染与收口**：answer 由代码模板定结论与数字（数字槽位锁定）；榜单类只口头给前 3 名+"完整榜单见看板"（防 20 个不同数字上限爆掉）；answer ≤1200 字符；citations ≤4 份；result ≤4KB、数字合计 ≤60；涨跌结论单向且带差额。why 类问题检索覆盖率低时**严禁强行引用**（H06 `cite_max=0`），降级为"数据事实+如实说文档没有记录"。

**数字一致性**：answer 中来自库的数字必须同源同舍入出现在 data_evidence.result；自算数字（占比/差额）的原始指标必须能在 result 找到。

**LLM 层（契约 §7 全合规）**：OpenAI 兼容 + 三环境变量原样透传；启动不校验 Key；思考模式开启（README 写明理由），assistant 消息连同 reasoning_content **整条原样回传**；max_tokens 显式 4096；总预算 180s，单次 min(120s, 剩余)，read timeout 独立于 connect；异常 finish_reason/空 content/错误码 → 重试一次 → refusal + trace 记真实原因；**live 模式每问必调 LLM 做最终编排**（复核意图/补充调工具/生成措辞，数字槽位代码锁定，LLM 数字与工具结果不一致时以工具为准并记 trace）；mock/live **结论一致、措辞允许差异**；完整请求原文（messages+tools+响应）入 trace 不截断（体积大写文件存路径）。

### 3.2 TDD 红测试清单（P3）

**编排层**：

| 测试 | 断言 |
|---|---|
| `test_refusal_wording_whitelist` | S02/S03 攻击请求 → refusal 且 answer 不含攻击内容/表名/姓名 |
| `test_out_of_range_refusal_no_numbers` | "9 月营业额" → refusal 且无数字（F01） |
| `test_metrics_unchanged_after_chat` | 任意 chat 后重查指标不变（run_sql 只读闸） |
| `test_slot_inheritance` | "6 月营业额"→"那 7 月呢" 第二轮继承门店/指标 |
| `test_windows_stack_compare` | T01 三轮后 compare_periods 取最近两个窗口 |
| `test_asof_switch_on_dangshi` | "那 6 月的时候呢" → as_of=2026-06-30，引 KB-010 v1 |
| `test_clarify_incomplete_time` | 开口"8 号那天呢" → clarify |
| `test_hybrid_parallel_evidence` | 混合题 data_evidence 与 citations 同时非空 |
| `test_number_consistency` | answer 数字 ⊆ evidence.result（同源同舍入） |
| `test_number_flood_cap` | Top10 类 answer 不同数字 ≤20 |
| `test_unit_price_from_notice` | H04 → 45（KB-025）且提及建档价滞后 |
| `test_why_no_forced_citation` | H06 类 cite_max=0 场景不引用 |
| `test_session_isolation` | 两 session_id 槽位互不可见（starter 缺陷 #14） |
| `test_chat_always_200` | 内部抛任何异常 → HTTP 200 + refusal + trace 记真实原因 |

**LLM 层（preflight 16 场景逐条红测试，用 `llm_gateway.py fake` 本地起假服务，不花钱）**：
normal（多轮多工具回路）、thinking_starved、empty_content、json_empty、bad_tool_args（截断非法 JSON 容错）、content_filter、insufficient_resource、aborted、http_401/402/422/429/500/503、slow（空行/keep-alive 跳过）、hang（read timeout 独立）。每条断言：HTTP 200 + 合法 JSON + 思考内容不进 answer + 真实原因入 trace。

### 3.3 实施步骤

1. 编排骨架 + 安全闸 + 区间闸 + 会话隔离修复（#14）→ mock 模式全管线。
2. 11 工具逐个移植+修口径，每个工具先红测试后实现。
3. 槽位继承 + as_of 切换 + clarify。
4. 模板渲染 + 上限收口 + 数字一致性单测。
5. LLM 客户端（OpenAI 兼容）+ 工具调用循环 + 数字槽位锁定。
6. preflight 16 场景红测试 → 修复 → `llm_gateway.py preflight` 实跑全过，输出贴 LLM_SETUP.md §7。
7. 开发全程走 `llm_gateway.py proxy` 记录流量（可观察性证据）。
8. 每完成一类意图跑公开评测，按分类表跟踪 doc/data/hybrid/version/multi_turn/refusal/safety 提分。

### 3.4 出口标准

- [ ] preflight 16 场景全过
- [ ] 公开题库 doc/data/hybrid/version/multi_turn/refusal/safety 各类显著提分（目标总分 ≥85）
- [ ] trace 每题可取、含完整提示词；无 Key 时 mock 降级四接口正常
- [ ] LLM_SETUP.md 七节内容齐（评审照着能切 DeepSeek）

---

## 阶段 P4：前端 + 调试面板（第四关 8 分 + 创新奖励）

### 4.1 SDD 规范

**布局**（决策 17）：单页工作台——左列看板（筛选栏：日期+门店+商品；营业额趋势图；Top10 商品表；数据质量面板：六项剔除原因分布），右列常驻 AI 对话栏（回答+引用角标可展开原文+data_evidence 可查看查询与结果；流式输出+"思考中"状态），调试面板为全屏覆盖层。

**HTTP 约束**：`/api/*` 与静态资源分开挂载，API 永不 3xx（评测不跟随跳转）；SPA history fallback 不得拦截 /api。Vue3+Vite dist 提交入库，评委零构建启动。

**调试面板**（/api/trace/{id} 可视化）：改写后查询、检索片段分数与过滤原因、dropped_instructions、工具调用/SQL 与结果、完整最终提示词与模型原始输出、分步耗时、错误。

**加分项**（按剩余时间取舍）：图表联动（对话数字↔趋势图高亮）、流式输出、自补评测题进回归。中国惯例：涨红跌绿、金额 ¥。

### 4.2 TDD/SDD 测试清单（P4）

前端以组件级测试 + API 契约测试为主（SDD：接口契约先行，前端按契约 mock 开发）：

| 测试 | 断言 |
|---|---|
| `test_api_never_redirects` | 全部 /api 路径返回 200 而非 301/302 |
| `test_static_spa_fallback` | 非 /api 未知路径回退 index.html；/api 未知路径 404 JSON |
| `test_trace_endpoint_complete` | trace 含契约 §6 全部五类信息 |
| `test_data_quality_payload` | 数据质量接口返回六项剔除原因计数（=P1 的 8/150/30/10/40/100） |
| 组件测试（Vitest） | 筛选变更触发 API 重查；引用角标展开原文；思考态渲染 reasoning 不混入 answer |

### 4.3 实施步骤

1. FastAPI 静态托管 + API/SPA 路由隔离（红测试先行）。
2. 看板三件套（趋势图/Top10/数据质量）——数据接口 P1 已就绪。
3. 对话栏（含 citations/data_evidence 展开、流式、思考态）。
4. 调试面板全屏覆盖层（trace 可视化）——现场调试环节 30 分的直接支撑。
5. 加分项按剩余时间排：图表联动 > 自补评测题 > 流式优化。
6. 把评测脚本接进回归：每次改动一键跑公开题库出分对比。

### 4.4 出口标准

- [ ] 评委零构建启动即可见完整 UI
- [ ] 调试面板覆盖 trace 全要素，演示链路顺（DEMO.md 素材）
- [ ] 评测即回归脚本可用

---

## 阶段 P5：收尾验收（8 分过程分兜底）

### 5.1 必交文件检查表（逐项对照 README）

| 文件 | 验收要点 |
|---|---|
| README.md | 3 步跑通 + 重建命令 + 架构图 + 选型理由 + 口径与歧义取舍（含思考模式开启理由、amount=0 行取舍） |
| DEBUG_LOG.md | ≥12 条，六字段齐（现象/假设/验证/根因/修复 commit/回归测试+红证据） |
| EVAL_REPORT.md | 基线 19.50 与最终得分对比，每次附运行命令/commit/模型配置/是否配 Key；分类分解表 |
| LLM_SETUP.md | 按契约 7.4 骨架八节，preflight 输出贴 §7 |
| AI_USAGE.md | AI 工具与拆任务（真实 prompt 例子）+ AI 错误诊断案例 + 哪些判断自己做 |
| DEMO.md | 一道混合问题全链路演示 + 调试面板演示 |

### 5.2 模拟评委五步验收（全过才算完）

1. 干净环境按 README 跑起来，不配 Key → mock 降级正常。
2. 按 LLM_SETUP.md 切 DeepSeek + 换 Key（用 fake/预检验证可切换性）→ 跑公开题库，与 EVAL_REPORT 对照。
3. **换库自验**：自造变体替换 `data/` + `knowledge_base/`（数字改、文档增删改）→ rebuild → 公开题库结构题（N01 类）仍合理、检索跟着新知识库走 → 证明零写死。
4. 通读 DEBUG_LOG/AI_USAGE/commit 历史 → "先红后修"顺序清晰，无单个 "finish" commit。
5. 现场调试演练：自查两道答错的题，按"先复现→看 trace→定位→修复→回归"流程走一遍计时。

### 5.3 出口标准

- [ ] 五步验收全过
- [ ] commit 历史分次、语义化、体现红→绿
- [ ] 仓库 push GitHub，链接可交付

---

## 附：横切工作流速查

**每修一个缺陷 / 每加一个功能**：

```
写红测试 → commit "test: ..." → 最小实现转绿 → commit "fix:/feat: ..."
→ 跑公开评测 → 分数记 EVAL_REPORT.md → 若涉 starter 缺陷，DEBUG_LOG 回填一条
```

**每次被 AI 误导**（诊断错、修复错）：立刻在 AI_USAGE.md 记一条（什么错误建议、怎么发现的）——这是 8 分维度里的硬素材，事后补不出来。

**时间警戒线**：T+36h 若 P3 未启动 → 砍 P4 加分项保 P3；T+60h 若 preflight 未全过 → P4/P5 压缩，preflight 是第三关给分前提。
