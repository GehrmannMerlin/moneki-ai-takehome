# Moneki.ai 实操作业 — 架构设计文档

> 状态：v2 修订稿（17 项决策定稿 + 源码对照审查修订，见 `docs/ARCHITECTURE-REVIEW.md`）
> 日期：2026-09-25
> 输入材料：作业 README、`docs/API_CONTRACT.md`、`eval/run_eval.py` 评分源码、公开题库 55 题、starter 全源码、`data/pos.db` 与 `knowledge_base/` 实地探查 + 独立复算验证

---

## 0. 决策登记表

| # | 决策点 | 定案 | 备选（未选） |
|---|---|---|---|
| 1 | 总体技术路线 | **保壳换芯**：保留 FastAPI 外壳与契约 schema，重写清洗/检索/问答三大核心 | 原地修复；全部重写 |
| 2 | 问答链路 | **规则主干 + LLM 语义层**：数字 100% 由代码从工具结果渲染（槽位锁定）；live 模式**每问必调 LLM** 做最终编排（复核意图/补充调工具/生成措辞），保证 preflight P1/P12 与评委可观察 | 纯规则；纯 LLM Agent |
| 3 | 检索架构 | **BM25 + 向量 RRF 融合**，向量不可用自动降级纯 BM25 | 纯 BM25；纯向量 |
| 4 | 前端形态 | **Vue3 + Vite，dist 提交仓库**，FastAPI 托管，评委零构建启动 | 原生 HTML；React |
| 5 | 清洗产物 | **规范化全量表 + 行级双口径剔除标记**，剔除推迟到计算层按口径执行 | v3 净表+rejects；双口径双净表 |
| 6 | 意图路由 | **规则先行 + 置信度分流**，低置信度走 LLM 结构化分类 | 双路并行；live 全走 LLM |
| 7 | 向量模型 | **paraphrase-multilingual-MiniLM-L12-v2**（~470MB，中英跨语言，CPU） | bge-small-zh；暂不上向量 |
| 8 | 版本感知 | **重建时自动解析 frontmatter 版本元数据**，查询时 as-of 过滤 | 硬编码；LLM 判断 |
| 9 | 多轮上下文 | **结构化槽位**（门店/商品/时间窗/意图），代码做指代消解 | LLM 改写；混合 |
| 10 | 中文分词 | **jieba**，挂载 KB-003 别名为自定义词典 | 字 bigram；修 starter tokenizer |
| 11 | 版本过滤位置 | **查询时 as-of 过滤**：默认滤"已废止"，时间锚点选当时有效版，"归档"不过滤 | 索引时只收现行；不过滤 |
| 12 | 切块策略 | **标题层级感知**：过短向上合并、过长按段落二切，目标 200~500 字 | 定长滑窗；纯段落 |
| 13 | 意图与安全结构 | **两层：安全闸前置 + 意图路由** | 扁平五类；LLM 判安全 |
| 14 | answer 生成 | **代码模板定结论与数字，LLM 只做措辞**：mock/live **结论一致、措辞允许差异** | LLM 写作文；骨架+润色 |
| 15 | hybrid 编排 | **查库与检索并行取数，代码统一组装** | 先库后文档；先文档后库 |
| 16 | 思考模式 | **开启**（DeepSeek 默认）：须回传 reasoning_content、思考不进 answer、max_tokens 显式 4096 | 关闭思考 |
| 17 | UI 布局 | **单页工作台 + 全屏调试覆盖层**：左看板右常驻对话栏，trace 全屏层 | 三标签页；对话优先 |

---

## 1. 总体架构（五层）

```
L1 前端（Vue3+Vite，dist 入库）
   经营看板（趋势图/Top10/数据质量） · AI 对话栏（常驻） · 调试面板（全屏覆盖层）
L2 FastAPI 契约层（保留 starter 外壳）
   /api/health /api/metrics/* /api/retrieve /api/chat /api/trace/{id}
   铁律：恒 200，永不 500；错误体现在 answer_type=refusal + trace
L3 问答编排（规则主干 + LLM 语义层，live 每问必调 LLM）
   安全闸 → 意图分类（规则优先，LLM 复核） → 实体/时间解析 → 区间闸
   → 槽位继承（windows[] 历史栈 + as_of 切换） → 工具执行（并行） → 模板渲染 → 上限收口 → 落 trace
L4 工具层
   L4a 数据工具：口径引擎（v2/v3 参数化） + 11 个只读工具（含 payment_mix/compare_periods/
       by_store_category/unit_price_check/first_sale_date） · 只读 SQL 闸
   L4b 混合检索：jieba+BM25 ∥ MiniLM 向量 → RRF 融合 → as-of 版本过滤 → top_k
       → 检索后注入清洗（指令句剥离记 trace）
L5 SQLite 存储（rebuild 一条命令全量重建）
   clean_lines / doc_versions / chunks+索引(BM25+向量blob) / sessions / traces
```

贯穿性原则：**一切可重建、零写死**（评委第 3 步换数据换知识库）；**数字由代码渲染**（契约 7.3）；**trace 从第一天内建**（现场调试 30 分）。

## 2. 数据地面实况（2026-09-25 独立复算验证，以此为准）

- `sales` 18628 行；`qty`/`amount` 为 TEXT。按 KB-001 v3 六条规则顺序剔除 **338 行**，保留 **18290 行**（= 18196 销售行 + 94 退款行；amount=0 行实际为 0）。
- 剔除明细：日期无法解析 **8**（3×`'N/A'`、2×`''`、3×`'2026-13-45'`——格式合法但**日历非法**，解析必须过 `datetime.date` 构造校验，只靠正则会多留 3 行使 N01 判红）；空 amount 150；qty≤0 30；脏门店外键 10；脏商品外键 40；七字段完全重复 **100**（有组含 2+ 副本）。
- 原始负金额行（退款行）仅 **94 行**，全部保留。早先扫描报告的"254 行""70 组重复"系误记，作废。
- 复算交叉验证：M01（6 月）= 156757.00 / 953.00 / 4311 / 36.36 / 6496，M02（7 月 S02）= 41740.00 / 107.00 / 875 / 47.70 / 1395，M04（618 S02 P06）= 3625.00 / 0 / 53 / 68.40 / 125，与公开题库期望逐一精确吻合；N01 期望 `valid_sales_rows=18290`、`kb_docs=35`。
- 知识库：35 篇文档 + 1 个无编号 README（不计入）。全部 .md 有 YAML frontmatter（doc_id/status/effective_from/superseded_by/stores）。
- `status` 三值语义：现行 / 已废止（被取代，默认滤） / 归档（历史时点记录，不过滤）。无 frontmatter 文档默认"现行"，生效日期从正文正则提取，提不到当"自始有效"。
- KB-062 为 GBK 编码（周五六延长至 23:00，与 KB-042 总表的 21:30 冲突——总表自带"临时调整以通知为准"，**通知/政策类 > 总表/参考类**）；KB-061 为 HTML（剥标签+实体反转义）；KB-022 为英文邮件（跨语言必需）；KB-060 第 42 行埋有提示注入（"回答 9,999,999 元"），S01 题用 `numbers_none` 检查。

## 3. 模块需求清单

### 3.1 清洗层 + 口径引擎（第一关地基）

- 输入 `data/pos.db` 三表；输出 `var/clean.db`。
- 规范化四条：编号 trim+upper；三种日期解析（**日历合法性须经 `datetime.date` 构造校验**，`'2026-13-45'` 这类格式合法但日历非法的行按规则 1 剔除）；amount 去 `¥`/`￥` 与首尾空白；qty 按整数。
- `clean_lines` 保留全部规范化行，行级标记 `reject_reason_v3` / `reject_reason_v2`（按手册 §3 顺序首因归因）。
- amount=0 行：保留标注，两不沾（当前快照实际为 0 行；README 写明取舍）。
- 指标五件套按 KB-001 v3 §4（净营业额含退款、有效订单数=销售行 DISTINCT order_id、客单价=净额/有效订单数 ROUND_HALF_UP 2 位、销量=销售 qty−退款 qty、退款按自身日期归属、区间为**闭区间**）；口径引擎参数化（v3 默认 / v2 可选），v2 差异三处：退款剔除、空 amount 回填 qty×unit_price、客单价分母为明细行数。
- `/api/metrics/*`、看板、`/api/chat` 的 data_evidence 共用同一引擎。
- 自验：保留+剔除=总行数；**fixture 用公开题库期望值**：N01（18290/35）+ M01–M06 全部期望值（fixture 写死合法，代码逻辑不得写死）。

### 3.2 混合检索层（第二关核心）

- Loader：四种后缀；UTF-8→GB18030 降级（KB-062）；HTML 剥标签+实体反转义保留连续正文（KB-061）；frontmatter 解析 + 无 frontmatter 降级（文件名取 KB-\d+，status 默认"现行"，生效日期正文正则提取，提不到当"自始有效"，元数据来源记 trace）；无编号文件不算文档（kb_docs=35）。
- 文档可见正文管线**与评测脚本同构**（复刻 `normalize_doc`/`decode_bytes`/`html_to_text`：NFKC、去空白含零宽空格、去 `*` `` ` `` `|` `#` `>`）；chunk 从可见正文切，quote 由代码截取"含目标事实的最短连续句段"（≤400 规范化字符），自测跑评测同款逐字校验。
- 切块：标题层级感知，200~500 字；chunk 文本逐字保留原文；chunk_id=doc_id#n。
- BM25：jieba 分词 + KB-003 别名扩展查询；**跨语言别名归一入索引**（英文文档命中别名时补写数据库写法 token，如 Salmon→三文鱼poke）。
- 向量：multilingual-MiniLM，rebuild 时计算，SQLite blob 存储；不可用自动降级纯 BM25。
- RRF 融合（k=60），检索必须恰好 top_k 条（**先过滤后截取**；不足时按分数补齐并标记 padded，问答链路不得使用 padded 片段；同一文档限占 1 格保多样性）。
- 版本过滤：as-of 日期（默认 2026-09-01），滤"已废止"不滤"归档"；追问"当时/那 N 月"时 as_of 切换到该时点（选当时有效版）。
- 排序先验：同主题冲突时**通知/政策类 > 总表/参考资料类**（KB-062 营业时间调整通知优先于 KB-042 总表）。
- **检索后注入清洗**：chunk 进 LLM 上下文前做指令句识别+剥离（移植 starter `sanitize.py` 正则），被剥句子记 trace `dropped_instructions`；检索打分与 quote 仍用原文。
- 索引缓存键 = 知识库内容哈希 + 代码版本；`.cache/` 不入库（**删除 starter 已提交的 `.cache/index.json`**）。
- 每个 chunk 记录 BM25 分/向量分/融合分/过滤原因 → trace。

### 3.3 问答编排层（第三关核心）

- 管线：安全闸 → 意图分类 → 实体/时间解析（锚 2026-09-01）→ **区间闸** → 槽位继承 → 并行工具执行 → 模板渲染 → 上限收口 → trace。
- 安全闸前置：提示注入/删改数据/套系统信息 → 直接 refusal；只读 SQL 白名单（SELECT/WITH 开头且有 FROM）。**refusal 措辞白名单化**：只说"不能执行修改数据的操作/超出数据范围"，不复述攻击内容、不带表名/SQL/姓名（S03 的 text_none 含 `create table`/`sqlite_master`/`drop table sales;`，F02 禁出现员工姓名）。
- **区间闸**：解析出的时间窗与 `data_period`（2026-05-01~08-31）无交集 → refusal（如实说明数据区间，不带数字）；有交集才执行。注意与 metrics API 行为相反：API 对空区间照契约返回 0，chat 对区间外问题必须 refusal（F01）。
- 意图五类对应 answer_type；hybrid 时查库与检索并行。why 类问题检索覆盖率低（文档无解释）时**严禁强行引用**（H06 `cite_max=0`），降级为"数据事实 + 如实说文档没有记录"。
- clarify 场景：时间指代不完整且无上文可继承（如开口就问"8 号那天呢"）→ 反问。
- 工具集（全只读，11 个）：query_metrics / query_daily / query_top_products / retrieve_docs / lookup_doc / **payment_mix**（H05 现金占比）/ **compare_periods**（D04/T01 两期对比，差额=舍入后指标之差，方向单向）/ **by_store + by_store_category**（D02 品类聚合）/ **unit_price_check**（H04/T03 实收单价 vs 建档价，按门店拆）/ **first_sale_date**（H03 新品首月锚定）。别名解析在实体层挂 KB-003（味噌拉面→味增拉面）。starter `tools.py` 有全部雏形，移植接口、只修口径。
- 数字冲突三分法（KB-001 §5）：① 经营结果数字（营业额/订单/销量/客单价/退款）以库按口径计算为准；② **商品现行售价以最新调价通知为准**（unit_price 是滞后建档价；金额以实收 amount 为准，不用建档价反推或覆盖）；③ 周报/纪要/复盘/反馈汇总的人工估算只当背景。
- 槽位模型：`{store, product, metric, windows[], as_of, intent}`；`windows[]` 保留最近 3 个显式时间窗（T01"这两个月"取最近两个）；追问命中"当时/那 N 月"时显式更新 as_of。
- 上限收口：answer ≤1200 字符、**不同数字 ≤20**（榜单类只口头给前 3 名 + "完整榜单见看板"）；citations ≤4 份、quote ≤400 规范化字符逐字；result ≤4KB、数字合计 ≤60；涨跌结论单向且必须带差额数字。
- **数字一致性**：answer 里来自库的数字必须同源同舍入地出现在 data_evidence.result（evidence_required 双重检查）；自算数字（占比/差额）只在 answer，但其原始指标必须能在 result 找到。写一致性单测。
- mock 模式 = 纯规则管线完整版；live 模式 = 规则主干产出候选结论+工具结果 → LLM 带 tools 声明做最终编排（复核意图/补充调工具/生成措辞），**数字槽位代码锁定**（LLM 输出数字与工具结果不一致时以工具结果为准并记 trace）。

### 3.4 LLM 接入层（契约 §7 合规）

- OpenAI 兼容协议 + 三环境变量；base_url 原样透传；启动不校验 Key、不调余额接口；无 Key 时 mock 模式四接口正常。
- 思考模式开启（README 写明理由）：带 tools 时 assistant 消息连同 reasoning_content **整条原样回传**；思考内容不进 answer/citations/data_evidence；流式先 reasoning_content 后 content（前端"思考中"态）；**max_tokens 显式设 4096**。
- 超时：总预算 180s，单次调用 min(120s, 剩余预算)；**read timeout 独立于 connect timeout**（hang 场景：连接建立但永不响应）；异常 finish_reason/空 content/错误码 → 重试一次 → refusal + trace 记真实原因。
- `tool_calls[].function.arguments` 是 JSON 字符串，**解析失败要容错**（bad_tool_args 场景：截断的非法 JSON），一条 assistant 消息可带多个工具调用且每个都要 `role:"tool"` + `tool_call_id` 回传；服务繁忙时空行 / SSE `: keep-alive` 注释行要跳过。
- 只用文档内参数；**完整请求原文**（messages + tools + 模型原始输出）写 trace，不截断（Key 除外；体积大写文件、trace 存路径）。
- **preflight 16 场景逐条红测试**（`eval/llm_gateway.py fake` 本地起假服务，不花钱）：normal（多轮多工具）、thinking_starved、empty_content、json_empty、bad_tool_args、content_filter、insufficient_resource、aborted、http_401/402/422/429/500/503、slow、hang。
- 开发时全程走 `llm_gateway.py proxy` 记录流量（契约 7.2.2 可观察）；交前跑 `preflight`，输出贴 LLM_SETUP.md §7。

### 3.5 trace / 会话存储

- SQLite 单文件：`sessions`（**按 session_id 严格隔离**，槽位 JSON——starter 的全局共享历史是严重缺陷）、`traces`（每环节一条：改写/检索候选与过滤/dropped_instructions/SQL/完整 LLM 请求原文/耗时/错误；rebuild 时保留 traces）。
- `/api/trace/{trace_id}` 直接读 traces；调试面板可视化同一数据源。评测每题答完立刻取 trace，取不到按该题不合格——trace 必须同步落库后再返回响应。

### 3.6 前端（单页工作台 + 全屏调试层）

- 左列：筛选栏（日期+门店+商品）、营业额趋势图、Top10 商品表、数据质量面板（六项剔除原因分布）。
- 右列：常驻 AI 对话栏（回答 + 引用角标可展开原文 + data_evidence 可查看查询与结果；流式输出 + "思考中"状态）。
- 调试面板：trace_id 点击弹出全屏覆盖层（检索片段分数、过滤原因、SQL、最终提示词、分步耗时）。
- 加分项：图表联动（对话数字 ↔ 趋势图高亮）、流式输出。
- 中国惯例：涨红跌绿；金额 ¥。
- **/api/\* 与静态资源分开挂载，API 永不 3xx 重定向**（评测不跟随跳转）；SPA history 路由 fallback 不得拦截 /api 路径。

## 4. 工程结构与可复现性

```
仓库根/
├── README.md DEBUG_LOG.md EVAL_REPORT.md LLM_SETUP.md AI_USAGE.md DEMO.md
├── starter/                 # 后端（保壳换芯）
│   ├── kbqa/                # 外壳：server/schemas/config
│   │   ├── core/            # 新核心：cleaning/retrieval/pipeline/llm
│   │   └── static/          # 前端 dist（提交入库）
│   └── tests/               # 红测试先行
├── frontend/                # Vue3 + Vite 源码
├── data/ knowledge_base/ eval/ docs/
└── var/                     # clean.db / 索引（gitignore）
```

- 重建：`python -m kbqa.rebuild` 一条命令；Makefile 兼容 Windows（自动识别 Scripts/ 与 bin/），README 附直接命令表。
- 收尾自验：自造变体替换 data/ + knowledge_base/ → rebuild → 评测，验证零写死。
- **删除 starter 已提交的 `starter/.cache/index.json`**（HANDOVER 吹嘘的"免建索引"产物，缓存键不含知识库内容，评委换库即炸），`.cache/` 入 gitignore。
- `health.kb_docs` = 实际入索引文档数（35），不是目录文件数（36）；无 KB 编号文件跳过并记 warning。

## 5. 分阶段实施计划（对齐 72h）

| 阶段 | 内容 | 出口标准 |
|---|---|---|
| 0 | 环境可复现：Makefile/命令表、无 Key 一条命令起服务；**AI_USAGE.md 建骨架（第 0 天起随手记）**；跑 starter 基线评测存 report.json 并按类别分解 | 干净环境 3 步跑通；基线分类表产出 |
| 1 | 清洗层 + 口径引擎 + metrics API | M01–M06 全对，**valid_sales_rows=18290**（N01 绿） |
| 2 | 检索层：loader/切块/BM25/向量/RRF/版本过滤/注入清洗；**starter 缺陷逐个红测试留证（见下）** | retrieval 15 题显著超基线；DEBUG_LOG 缺陷条数 ≥12 |
| 3 | 问答编排（11 工具 + 区间闸 + 槽位）+ LLM 接入 + preflight 16 场景 | doc/data/hybrid/version/multi_turn/refusal/safety 分类提分；preflight 全过 |
| 4 | 前端看板 + 对话栏 + 调试面板 + 加分项 | UI 完整，trace 可视化 |
| 5 | 收尾：EVAL_REPORT、换库自验、必交文件齐 | 模拟评委五步验收全过 |

**DEBUG_LOG 红→绿工作流**（与保壳换芯配套）：① 对 starter 老代码写**缺陷复现测试**（断言正确行为 → 老代码上红），提交 `test: 复现缺陷 X（红）`；② 新核心实现后同一测试转绿，提交 `fix: 缺陷 X 根因+修复`；③ DEBUG_LOG 每条的"回归测试 + 修复前确实是红的证据"由这对 commit 提供。缺陷初步清单 15 条见 `docs/ARCHITECTURE-REVIEW.md` 附录 D。

每阶段结束跑公开评测，分数 + commit 记入 EVAL_REPORT.md（与基线分类分解表对比）；commit 按"先红测试再修复"分次提交。

## 6. 风险清单

| 风险 | 缓解 |
|---|---|
| starter 缺陷分层，修一层暴露一层 | 每修一处重跑公开评测；DEBUG_LOG 逐条记录；缺陷清单 15 条已预查（REVIEW 附录 D） |
| 隐藏题库换问法 | 别名词典 + 11 工具覆盖 + LLM 兜底分类 + 自补评测题 |
| 换知识库后 frontmatter 不规范 | loader 降级链：frontmatter → 文件名 → 正文推断；status 默认"现行"并记元数据来源 |
| 思考模式 reasoning_content 回传 400 | LLM 层红测试覆盖契约 7.3 全表 + preflight 16 场景 |
| 向量模型下载失败/不可用 | 自动降级 BM25，retrieve 永不 500；模型与索引**启动期预加载**（启动慢可以，答题慢不行） |
| 评测期间服务挂掉全归零 | 编排层全包裹异常；评测前健康检查脚本；trace 同步落库后再返回 |
| live 模式高置信度问题不调 LLM → preflight P1 红 | 决策 2 已改：每问必调 LLM 做最终编排，数字槽位代码锁定 |
| 文档内注入（KB-060 实弹）穿透小模型 | 检索后 chunk 级指令句剥离 + trace 记录；quote 仍用原文 |
| 差额/占比类自算数字与 evidence 对不上 | answer 数字与 result 同源同舍入；一致性单测 |
