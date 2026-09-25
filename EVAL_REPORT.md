# 评测报告

公开题库 `eval/public_questions.jsonl`（55 题 / 100 分）的得分记录。
**每次得分都附上**：运行命令、代码 commit、用的模型与关键配置（不写 Key）、是否配置了 Key。

评分脚本是评委给的那一份，我们一行没改：

```
python eval/run_eval.py --base-url http://localhost:8000 --questions eval/public_questions.jsonl
```

分类分解表由 `starter/scripts/baseline_report.py` 生成（`make baseline`），
它只做聚合，类别/分值/题号都从题库和报告里读，没有写死任何数字。

---

## §0 基线：starter 原样，17.00 / 100

**这一节是全部后续分数的对照基准**，来自"接手时原封不动的 starter"。

| 项 | 值 |
|---|---|
| 得分 | **17.00 / 100.00（17.0%）**，11 题全绿 / 共 55 题 |
| 运行命令 | `python eval/run_eval.py --base-url http://localhost:8000 --questions eval/public_questions.jsonl --out eval/_baseline_raw`<br>（等价：`cd starter && make baseline`） |
| 代码 commit | `a41f6c1`（starter 业务代码与原始提交 `56f7a1f` 一致，未改一行） |
| 模型 | **无**（未配置任何 Key，`llm_mode=mock`） |
| 是否配置 Key | 否 |
| 服务 | `python -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000`，Windows / Python 3.12.6 |
| 原始报告 | `eval/_baseline_raw/report.json`、`eval/_baseline_raw/report.md` |
| 聚合报告 | `eval/baseline_report.json` |
| 每题耗时 | 中位数 0.016 秒，最大 0.078 秒，合计 1.4 秒（mock 模式，不调模型） |

### 分类分解表

| 类别 | 基线得分 | 满分 | 比例 | 全绿题数 | 失分题（未通过的检查项） |
|---|---|---|---|---|---|
| `metrics` 指标接口 | 1.00 | 6.00 | 16.7% | 1 / 6 | M01/M02/M03/M04（`expect.net_revenue`、`expect.aov`）、M06（`expect_days`） |
| `retrieval` 检索质量 | 6.00 | 15.00 | 40.0% | 6 / 15 | R01/R03/R04/R05/R08/R10/R11/R15（`gold_all`）、R13（`gold_any`） |
| `data` 纯数据问题 | 0.00 | 12.00 | 0.0% | 0 / 6 | D01–D06 全部（`evidence_required`、`numbers_all`） |
| `doc` 纯文档问题 | 0.00 | 16.00 | 0.0% | 0 / 8 | C01–C08 全部（`answer_type_in`、`cite_all`） |
| `version` 版本与时效 | 0.00 | 6.00 | 0.0% | 0 / 3 | V01–V03（`answer_type_in`、`cite_all`） |
| `hybrid` 数据 + 文档 | 0.00 | 18.00 | 0.0% | 0 / 6 | H01–H06（`answer_type_in`、`cite_all`、`evidence_required`、`cite_max`） |
| `multi_turn` 多轮追问 | 1.00 | 9.00 | 11.1% | 0 / 3 | T01（`answer_type_in`、`evidence_required`）、T02/T03（`cite_all`） |
| `refusal` 拒答 | 6.00 | 8.00 | 75.0% | 3 / 4 | F01（`answer_type_in`：区间外应拒答，实际没拒） |
| `safety` 安全 | 3.00 | 9.00 | 33.3% | 1 / 3 | S01（`answer_type_in`、`cite_all`）、S02（`answer_type_in`、`numbers_none_beyond_question`） |
| `health` 健康检查 | 0.00 | 1.00 | 0.0% | 0 / 1 | N01（`expect.kb_docs`、`expect.valid_sales_rows`） |
| **合计** | **17.00** | **100.00** | **17.0%** | **11 / 55** | |

### 基线 `/api/health` 快照（暴露了三个事实性错误）

```json
{
  "status": "ok",
  "llm_mode": "mock",
  "kb_docs": 36,
  "kb_chunks": 53,
  "valid_sales_rows": 18628,
  "today": "2026-09-01",
  "data_period": {"start": "", "end": "N/A"},
  "cleaning_report": {
    "raw_rows": 18628,
    "removed": {"1_unparseable_date": 0, "2_empty_amount": 0, "3_qty_le_zero": 0,
                "4_store_not_in_stores": 0, "5_product_not_in_products": 0,
                "6_duplicate_row": 0, "note_unparseable_amount": 0},
    "kept_rows": 18628, "kept_sales_rows": 18534, "kept_refund_rows": 94
  },
  "index_key": "8651fac326e2",
  "kb_warnings": ["跳过没有 KB 编号的文件：README.md"]
}
```

对着契约 §1 逐项看，三处如实性问题一眼可见：

| 字段 | 基线实际 | 应为 | 原因 |
|---|---|---|---|
| `kb_docs` | 36 | **35** | `service.py:70` 数的是目录里的文件数（含无编号的 `README.md`），不是入索引文档数 |
| `valid_sales_rows` | 18628 | **18290** | `cleaning.py:77-102` 根本不清洗：六项剔除规则一条没执行，`removed` 全 0 |
| `kb_chunks` | 53 | （35 篇文档按现行切块规则应远多于此） | `loader.py:12` 只收 `.md`/`.markdown`，丢掉 KB-022/061/062 三篇；`chunker.py:41` 又丢掉每篇文尾不足 300 字的部分 |

`data_period` 是 `{start: "", end: "N/A"}`——日期没规范化，`MIN()`/`MAX()` 直接排在脏值上。
它同时是"区间闸"（F01 类拒答题）的判据来源，所以这个脏值会顺着管线一路影响拒答行为。

### 关于 19.50 与 17.00 这两个基线数字

`docs/ARCHITECTURE-REVIEW.md` 与 `docs/IMPLEMENTATION-PLAN.md` 记录的上一次实测是 **19.50 / 100**。
本次在同一台机器、同一份 starter 代码（`56f7a1f`）上重跑，得到 **17.00 / 100**。
两次都在 mock 无 Key 模式下，差异集中在两类：

- `refusal`：F01 在两次运行中都不通过（区间外没拒答），但 F02–F04 的通过情况有波动；
- `retrieval`：R01–R15 里通过的是哪 6 题不完全一样（检索依赖 BM25 排序，且基线索引只有 53 块）。

**本报告一律以 17.00 为准**，原因是它带有完整的可复现证据（命令 + commit + 原始
`report.json` + 聚合 JSON），而 19.50 只有上一会话的结论记录。
两个数字都保留在这里，作为"基线本身也有波动"的诚实说明——后续每个阶段都比对
同一张分类表，而不是比对单个总分。

### 基线暴露的问题清单（按层，对应 DEBUG_LOG 的缺陷条目）

1. **数据层**：清洗层完全没实现（缺陷 #1）→ 全部 `data`/`hybrid`/`metrics` 题失分，`health` 红。
2. **指标层**：右开区间（#2）、v2 旧口径（#3）→ `metrics` 6 题只对 1 题。
3. **检索层**：分词按空白（#6）、loader 丢格式（#7/#8/#9）、chunk 丢文尾（#10）、
   缓存键不含知识库（#11）、doc_id 张冠李戴（#12）、先取后滤（#13）
   → `retrieval` 15 题丢 9 题，`doc`/`version` 全灭。
4. **编排层**：`kb_docs` 口径（#5）、会话全局共享（#14）、`run_sql` 可写（#4）
   → `safety`/`multi_turn`/`health` 失分。
5. **过程层**：自带 17 条测试是纯冒烟（只断 200 与非空），HANDOVER 三处撒谎（#15）
   → 缺陷能长期存活。

---

## §1 P1 之后（数据层 + 口径引擎 + metrics API）：42.50 / 100

| 项 | 值 |
|---|---|
| 得分 | **42.50 / 100.00（42.5%）**，25 题全绿 / 共 55 题（基线 17.00，11 题全绿） |
| 运行命令 | `python eval/run_eval.py --base-url http://localhost:8000 --questions eval/public_questions.jsonl --out eval/_p1_raw` |
| 代码 commit | `ace8d9a`（P1 三个 fix 提交的最后一个；本阶段从 `eb8b3e2` 起） |
| 模型 | **无**（未配置任何 Key，`llm_mode=mock`） |
| 是否配置 Key | 否 |
| 原始报告 | `eval/_p1_raw/report.json`、`eval/_p1_raw/report.md` |
| 聚合报告 | `eval/p1_report.json` |
| 每题耗时 | 中位数 0.016 秒，最大 0.079 秒，合计 1.6 秒 |
| 测试 | `pytest tests` → **85 passed, 2 failed**（2 条红的是缺陷 #11 的 P2 复现测试） |

### 分类对比：基线 → P1

| 类别 | 基线 | **P1** | 满分 | 变化 | 说明 |
|---|---|---|---|---|---|
| `metrics` 指标接口 | 1.00 | **6.00** | 6.00 | +5.00 | M01–M06 全绿：闭区间 + v3 口径 |
| `retrieval` 检索质量 | 6.00 | **7.00** | 15.00 | +1.00 | 只涨 1 题。分词/loader/chunker 是 P2 |
| `data` 纯数据问题 | 0.00 | **12.00** | 12.00 | **+12.00** | D01–D06 全绿 |
| `doc` 纯文档问题 | 0.00 | 0.00 | 16.00 | — | 检索层未动（P2） |
| `version` 版本与时效 | 0.00 | 0.00 | 6.00 | — | 同上 |
| `hybrid` 数据 + 文档 | 0.00 | **3.00** | 18.00 | +3.00 | H05 全绿（支付占比，纯数据侧）；其余卡在检索 |
| `multi_turn` 多轮追问 | 1.00 | **3.50** | 9.00 | +2.50 | 部分轮次的数据数字对了 |
| `refusal` 拒答 | 6.00 | **8.00** | 8.00 | +2.00 | **F01 转绿**（见下） |
| `safety` 安全 | 3.00 | 3.00 | 9.00 | — | S01/S02 卡在引用与措辞，P3 |
| `health` 健康检查 | 0.00 | 0.00 | 1.00 | — | `valid_sales_rows` 已绿，只差 `kb_docs`（P2） |
| **合计** | **17.00** | **42.50** | **100.00** | **+25.50** | 全绿题数 11 → 25 |

### 本阶段最重要的三个事实

**① `valid_sales_rows` 一次命中 18290。** 六项剔除 8/150/30/10/40/100，合计 338，
与独立复算逐项一致。其中日历非法日期 `'2026-13-45'` 恰好 3 行——
只靠正则解析会留下它们，`valid_sales_rows` 就变成 18293，N01 照样红。
所以日期解析过的是 `datetime.date` 构造校验，不是正则匹配。

**② F01 是意外收获，而且它证明了"按层修复"的连带效应。**
F01（"9 月的营业额是多少？"）在基线与 P1 的第一次实测里都是红的。
修好 `data_period` 之后它自己绿了——因为区间闸的判据正是
`MIN(date)/MAX(date)`，而基线里这个值是 `{start: "", end: "N/A"}`（日期没规范化）。
现在的回答是：

> 数据库里只有 2026-05-01 至 2026-08-31 的销售明细，2026-09-01 至 2026-09-30 没有任何数据。
>
> `answer_type=refusal`，且经 `numbers_none_beyond_question` 检查确认没有编造数字。

**③ `metrics` 全绿靠的是"只有一个指标出口"。** 看板、`/api/metrics/*`、
问答的 `data_evidence` 三处都调 `MetricsEngine`，所以接口对不上口径这件事
在结构上就不成立，不依赖"记得改两处"。

### N01 的剩余 1 分与它为什么留到 P2

`N01` 要求 `kb_docs == 35` 且 `valid_sales_rows == 18290`。现在后者绿、前者是 36：

```json
{"kb_docs": 36, "kb_chunks": 80, "valid_sales_rows": 18290,
 "data_period": {"start": "2026-05-01", "end": "2026-08-31"}}
```

`kb_docs=36` 有两层原因，都属于 P2 的 loader/索引：
`service.py` 数的是目录文件数（含无编号的 `README.md`），
而 `loader.py:12` 又不收 `.txt`/`.html`，所以真正入索引的只有 32 篇
（`kb_chunks=80`，35 篇应为更多）。P1 按计划不动检索层，
所以这里**故意留着不修**——提前改会让 N01 的失败原因变得含糊。

---

## §2 P2 之后（检索层）

_待 P2 完成后填写。目标：`retrieval` 15 题接近满分、`doc`/`version` 起步、
`kb_docs=35`，N01 全绿。_

---

## §3 P3 之后（问答编排 + LLM 接入）

_待 P3 完成后填写。这一节要附上配置了 Key 的最终得分（DeepSeek `deepseek-flash`），
以及 `LLM_SETUP.md` §7 的 preflight 输出作为"切得过去"的旁证。_

---

## §4 P4/P5 之后（前端、调试面板、收尾）

_待 P4/P5 完成后填写。_

---

## 附：怎么复现这张表

```bash
# 1) 起服务（终端 A，从仓库根目录）
cd starter
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # POSIX: .venv/bin/python
.venv/Scripts/python -m kbqa.rebuild
.venv/Scripts/python -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000

# 2) 跑基线并生成分类表（终端 B，从仓库根目录）
python eval/run_eval.py --base-url http://localhost:8000 \
    --questions eval/public_questions.jsonl --out eval/_baseline_raw
cd starter && .venv/Scripts/python scripts/baseline_report.py --markdown
```
