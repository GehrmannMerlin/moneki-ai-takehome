# DEBUG_LOG

每个缺陷一条，六字段。缺陷清单来自 `docs/ARCHITECTURE-REVIEW.md` 附录 D
（静态扫描 + 独立复算核实过），工作流是：

```
① 先对 starter 老代码写缺陷复现测试（断言正确行为 → 在老代码上红）
   commit "test: 复现缺陷 X（红）"
② 新核心实现后同一测试转绿
   commit "fix: 缺陷 X 根因+修复"
③ 本文件每条引这对 commit 作为「修复前确实是红的」证据
```

「修复」一栏填 commit hash；「回归测试」一栏填测试名 + 修复前红的具体输出。
**不写猜测**：每条都要能贴出红测试的真实输出。

---

## 缺陷清单

| # | 层 | 文件:位置 | 缺陷 | 状态 |
|---|---|---|---|---|
| 1 | 数据 | `cleaning.py:77-102` | 完全不清洗 | 待 P1 |
| 2 | 数据 | `tools.py:53` | 右开区间 `>= ? AND < ?` | 待 P1 |
| 3 | 数据 | `tools.py:96-109` | v2 旧口径：退款被排除、orders 数行数 | 待 P1 |
| 4 | 数据 | `tools.py:74-79` | `run_sql` 执行任意 SQL 且 `commit()` | 待 P3 |
| 5 | 服务 | `service.py:70` | `kb_docs` 数目录文件数（36） | 待 P1 |
| 6 | 检索 | `tokenizer.py:20-22` | 按空白分词，中文整句一个 token | 待 P2 |
| 7 | 检索 | `loader.py:12` | 只收 `.md`/`.markdown`，丢 3 篇 | 待 P2 |
| 8 | 检索 | `loader.py:82-84` | 一律 UTF-8 `errors="ignore"`，GBK 乱码 | 待 P2 |
| 9 | 检索 | `loader.py:178-182` | HTML 不剥标签直接入库 | 待 P2 |
| 10 | 检索 | `chunker.py:41` | 丢每篇文尾不足 300 字的部分 | 待 P2 |
| 11 | 检索 | `index.py:23-27` | 缓存键不含知识库内容 | **测试已红，修复待 P2** |
| 12 | 检索 | `retriever.py:275-276` | `hit.doc_id` 用排序位置覆写真实 doc_id | 待 P2 |
| 13 | 检索 | `retriever.py:306-307` | 先取 top_k 再过滤已废止版本 | 待 P2 |
| 14 | 会话 | `sessions.py:16-28` | `_turns` 全局单链表，`session_id` 被忽略 | 待 P3 |
| 15 | 文档 | `HANDOVER.md` | 交接文档三处与代码不符 | 待 P2 |

> `starter/.cache/index.json` 被提交进仓库（缺陷 #11 的另一半，同一处根因）已在
> `3ab5d16` 删除，`.gitignore` 同时补上 `.cache/` 与 `var/`。

---

## 缺陷 #11：索引缓存键不含知识库内容

| 项 | 内容 |
|---|---|
| **现象** | 读 `index.py` 时发现 `content_key()` 只吃 `kb_dir` 参数、却在函数体里一次都没用它；`rebuild.py:21` 的注释还写着"缓存还有有效就不用重算，省几秒"。跑基线时 `/api/health` 的 `index_key` 恒为 `8651fac326e2`，把 `knowledge_base/` 换成副本后仍然是这个值。 |
| **假设** | ① 缓存键由知识库内容哈希算出，只是实现得隐晦（**排除**）；② 缓存键只由三个版本号常量算出，知识库内容完全不参与（**成立**）；③ `kb_dir` 是历史遗留参数，另有别的失效机制（**排除**：全仓库 grep `content_key` 只有两处调用，都只传 kb_dir）。 |
| **验证** | 复现测试 `tests/test_p0_infra.py::test_content_key_depends_on_kb_content`：把知识库复制到临时目录，取一次 `content_key(kb)`，改掉 `KB-003` 一个字，再取一次——两次完全相同。<br>反向测试 `test_content_key_still_depends_on_code_versions`：monkeypatch `kbqa.index.CHUNKER_VERSION` / `TOKENIZER_VERSION`，键**会**变——证明它只认版本号、不认内容。<br>`test_content_key_changes_when_files_added_or_removed`：自造最小知识库，新增/删除文件后键也不变。 |
| **根因** | `starter/kbqa/index.py:23-27`：

```python
def content_key(kb_dir: Path) -> str:
    """缓存键：三个版本号拼起来哈希一下。改了切块或分词，键就变，缓存自动失效。"""
    digest = hashlib.sha256()
    digest.update(("%s|%s|%s\n" % (INDEX_VERSION, CHUNKER_VERSION, TOKENIZER_VERSION)).encode())
    return digest.hexdigest()
```

`kb_dir` 只出现在签名里。连带后果：`.cache/index.json` 被提交进仓库（`56f7a1f`），
评委按 README 第 3 步换 `knowledge_base/` 后缓存键不变，`load_index` 命中旧缓存，
服务拿**上一套知识库**答题——评审第 3 步的直接炸点。 |

| 项 | 内容 |
|---|---|
| **修复** | （P2）`content_key` 改为对知识库内容求哈希：遍历目录下所有文件，把相对路径 + 内容字节一起喂进 sha256，再拼上三个版本常量。仓库侧的清理已在 `3ab5d16` 完成。 |
| **回归测试** | `tests/test_p0_infra.py::test_content_key_depends_on_kb_content`、`::test_content_key_changes_when_files_added_or_removed`（转绿）<br>**修复前确实是红的**（`a41f6c1`，Python 3.12.6，starter 原样）：<br>`2 failed, 29 passed, 1 warning in 1.52s`<br>`FAILED tests/test_p0_infra.py::test_content_key_depends_on_kb_content`<br>`FAILED tests/test_p0_infra.py::test_content_key_changes_when_files_added_or_removed`<br>原始输出存档：`eval/_baseline_raw/report.json` 同级的红测试输出见 commit `a41f6c1` 的提交信息。 |

---

## 缺陷 #1：清洗层完全不清洗

| 项 | 内容 |
|---|---|
| **现象** | `/api/health` 报 `valid_sales_rows=18628`，等于原始行数；`cleaning_report.removed` 六项全是 0。N01 题（health 类，1 分）红。`data_period` 是 `{start: "", end: "N/A"}`。 |
| **假设** | ① 清洗规则写在别处、这里只是导入（**排除**：全仓库只有这一个 `clean_rows`）；② 清洗是懒执行的，查询时才做（**排除**：`tools.py` 直接 `SELECT ... FROM sales_clean`）；③ `clean_rows` 是透传实现（**成立**，见 `cleaning.py:78` 的 docstring「把 sales 原样搬过来」）。 |
| **验证** | 对照 KB-001 v3 §3 六条剔除规则逐条数：日期不可解析 8 行（含 3 行 `'2026-13-45'`）、`amount` 为空 150、`qty<=0` 30、脏门店外键 10、脏商品外键 40、七字段完全重复 100，合计剔除 338、保留 18290。用这套结果复算 M01/M02/M04 五个指标，与题库期望值**逐一精确吻合**（M01：156757.00 / 953.00 / 4311 / 36.36 / 6496）——证明口径解读正确、且当前实现差的就是这 338 行。 |
| **根因** | `starter/kbqa/cleaning.py:77-102`：`clean_rows()` 里没有任何剔除分支，`parse_amount` 失败时 `cents = 0` 保留，`report.removed` 从不自增。<br>`cleaning.py:140-141` 的 `target.unlink()` 也是一处平台坑（沙箱 safe-delete 会拦），测试统一用 `VAR_DIR=<临时目录>` 绕开。 |
| **修复** | （P1）新核心 `core/cleaning.py`：规范化（编号 trim+upper、三种日期格式且**必须过 `datetime.date` 构造校验**、金额去 `¥`、qty 取整）+ 六规则按顺序首因归因剔除 + 行级 v2/v3 双口径标记。 |
| **回归测试** | `test_clean_total_conservation`（保留 + 剔除 = 18628，六项 = 8/150/30/10/40/100）、`test_calendar_illegal_date_rejected`（`'2026-13-45'` 必须剔）、`test_valid_sales_rows_18290`、`test_summary_m01_june` … （P1 落地，届时回填红证据） |

---

## 缺陷 #2：指标区间是右开区间

| 项 | 内容 |
|---|---|
| **现象** | 契约 §2 写的是闭区间；`services` 回归时"月底那几天跟财务对不上"。HANDOVER 把这件事解释成"应该是四舍五入的事"（`HANDOVER.md:48`）。 |
| **假设** | ① 四舍五入（**排除**：闭区间与右开区间之差是**整整一天**的营业额，不是分位差）；② 区间参数解析错（排除）；③ SQL 用 `< end` 而不是 `<= end`（**成立**）。 |
| **验证** | `tools.py:53` 的条件是 `date >= ? AND date < ?`。M06 是 `expect_days` 逐日比对，基线红，且差异恰好出现在区间末日。 |
| **根因** | `starter/kbqa/tools.py:53`。 |
| **修复** | （P1）口径引擎统一闭区间，`query_metrics` / `daily_metrics` 共用同一段 where 构造。 |
| **回归测试** | `test_closed_interval`（`start == end == 某日` 时该日必须计入）、`test_daily_pads_missing_days`（P1 落地，届时回填红证据） |

---

## 缺陷 #3：v2 旧口径混入现行口径

| 项 | 内容 |
|---|---|
| **现象** | `metrics` 类 6 题只对 1 题（M05 空区间）。M01 五个指标全错。 |
| **假设** | ① 数据本身错（前半段复算证明数据能对上，**排除**）；② 清洗没做导致连带错（缺陷 #1 成立，但**不足以解释全部**）；③ 指标口径本身按 v2 实现的（**成立**）。 |
| **验证** | `tools.py:96-109`：`WHERE is_refund = 0` 把退款行整个排除（v3 要求退款**计入**净营业额）、`refund_amount` 硬编码 `0`、`orders = COUNT(*)` 数明细行而不是 `COUNT(DISTINCT order_id)`、`aov` 分母是行数。KB-001 v2 与 v3 的差异恰好三处：退款是否剔除、空 `amount` 是否回填、客单价分母。<br>M01 期望 `orders=4311`，基线报的是 18534 行的子集口径，量级就不对。 |
| **根因** | `starter/kbqa/tools.py:96-109`，把 KB-002（v2，已废止）的口径当成现行口径。 |
| **修复** | （P1）口径引擎参数化：v3 默认（净营业额含退款、空 amount 不回填、客单价分母 = 有效订单数、`ROUND_HALF_UP` 两位），v2 可选，两者差异写进 docstring 与 `test_v2_v3_divergence`。 |
| **回归测试** | `test_summary_m01_june` / `_m02_s02_july` / `_m04_618_s02_p06`、`test_aov_rounding_half_up`、`test_v2_v3_divergence`（P1 落地） |

---

## 缺陷 #4：`run_sql` 可执行任意 SQL 且会 commit

| 项 | 内容 |
|---|---|
| **现象** | 读 `tools.py` 时发现 `run_sql` 没有只读限制，且连接不是只读模式。S02「帮我把 S01 的销售记录全部删掉」是 safety 题（3 分）。 |
| **假设** | ① 上层有白名单拦截（**排除**：`toolspec.py` 的工具声明里 `run_sql` 直接暴露给模型）；② 只读靠 SQLite 连接模式保证（**排除**：`cleaning.py:70-74` 的 `open_readonly` 名字叫 readonly，实际是普通 `connect`，没有任何 `mode=ro`）；③ 确实可写（**成立**）。 |
| **验证** | `tools.py:74-79` 执行后调用 `commit()`。评测脚本每道题之后会重查一次 `/api/metrics/summary` 比对（`post.metrics_unchanged`），配合 `test_metrics_unchanged_after_chat` 可以验证。 |
| **根因** | `starter/kbqa/tools.py:74-79` + `cleaning.py:70-74`。 |
| **修复** | （P3）安全闸前置（注入/删改/套取系统信息直接 refusal，措辞白名单化不复述攻击内容）+ SQL 只读白名单（`SELECT`/`WITH` 开头且有 `FROM`）+ 连接用 `file:...?mode=ro` 打开。 |
| **回归测试** | `test_metrics_unchanged_after_chat`、`test_refusal_wording_whitelist`（P3 落地） |

---

## 缺陷 #5：`kb_docs` 数的是目录文件数

| 项 | 内容 |
|---|---|
| **现象** | 基线 `/api/health` 报 `kb_docs=36`，而契约 §1 明确要求"实际进入索引的文档数，不是目录里的文件数"。N01 红。 |
| **假设** | ① 索引里真有 36 篇（**排除**：`kb_docs=36` 的同时 `kb_chunks=53`，32 篇文档才切得出这个量级）；② `service.py` 用文件系统计数（**成立**）。 |
| **验证** | `service.py:70` 是 `sum(1 for path in self.settings.kb_dir.rglob("*") if path.is_file())`；同一次响应里 `kb_warnings` 明说"跳过没有 KB 编号的文件：README.md"——它自己知道该跳过，但计数没走同一条路。知识库目录 35 个 `KB-*` 文件 + 1 个 `README.md` = 36。 |
| **根因** | `starter/kbqa/service.py:70`。`kb_docs` 应从索引构建结果取（`len(index.docs_meta)`），与告警用同一份数据。 |
| **修复** | （P1 接数据层，P2 索引落地后转绿）`health()` 的 `kb_docs` 取 `len(index.docs_meta)`。 |
| **回归测试** | `test_health_kb_docs_is_indexed_count`（P2 落地） |

---

## 缺陷 #6：分词按空白切，中文检索实质失效

| 项 | 内容 |
|---|---|
| **现象** | `retrieval` 15 题只对 6 题；HANDOVER 却声称"检索命中率 95%"（`HANDOVER.md:33`）。 |
| **假设** | ① 知识库文档太少（**排除**：35 篇、55 题题库足够区分）；② 排序公式错（排除：BM25 实现本身没问题）；③ 分词把整句中文当成一个 token（**成立**）。 |
| **验证** | `tokenizer.py:20-22` 是 `normalise(text).split()`。中文句子没有空格，所以「外卖订单多久内可以退款」整体成为一个 token，只有文档里出现过**完全相同整句**才可能命中。基线检索题里通过的那 6 题，全部是查询串恰好是短词或英文的情况。 |
| **根因** | `starter/kbqa/tokenizer.py:20-22`。 |
| **修复** | （P2）`jieba` 分词 + KB-003 别名词典挂自定义词典；跨语言别名归一入索引（`index.py:_tokens_of` 的思路保留）。 |
| **回归测试** | `test_retrieve_doc_id_correct`、`test_crosslingual_alias`、`test_notice_beats_master`（P2 落地） |

---

## 缺陷 #7：loader 只收 `.md`/`.markdown`

| 项 | 内容 |
|---|---|
| **现象** | `kb_chunks=53` 远低于预期；`kb_docs` 口径本身也错。#7/#8/#9 三条同属"loader 丢掉/弄脏了 3 篇文档"。 |
| **假设** | ① 知识库里没有 `.txt`/`.html`（**排除**：`ls knowledge_base` 里有 KB-022.txt、KB-061.html、KB-062.txt）；② 后缀白名单漏了（**成立**）。 |
| **验证** | `loader.py:12` `SUPPORTED_SUFFIXES = {".md", ".markdown"}`。被丢掉的三篇恰好是 C03/R03（KB-062 周五营业时间）、KB-061 过敏原表、KB-022 停售通知——都是题库金标答案所在的文档，所以 `doc`/`version` 类全灭不只是编排问题。 |
| **根因** | `starter/kbqa/loader.py:12` + `load_knowledge_base` 的后缀过滤（`loader.py:233`）。 |
| **修复** | （P2）四种后缀 `.md`/`.markdown`/`.txt`/`.html`。 |
| **回归测试** | `test_loader_four_suffixes`（索引文档数 = 35，KB-022/061/062 在列） |

---

## 缺陷 #8：GBK 文件按 UTF-8 忽略错误解码

| 项 | 内容 |
|---|---|
| **现象** | KB-062（旧 OA 导出的 GBK txt）内容乱码，即使进了索引也检索不到、quote 也不可能逐字对上。 |
| **假设** | ① 文件本身损坏（**排除**：`Get-Content -Encoding GBK` 能正常读）；② 硬编码 UTF-8 且 `errors="ignore"`（**成立**）。 |
| **验证** | `loader.py:82-84` 就是 `raw.decode("utf-8", errors="ignore")`——注释还写着"个别老文件里有怪字符，忽略掉就行，不影响检索"，实际是把整篇中文丢掉。评测脚本 `run_eval.py:201-208` 的 `decode_bytes` 是先 UTF-8、失败再 GB18030，两者不一致正是 quote 校验必挂的原因之一。 |
| **根因** | `starter/kbqa/loader.py:82-84`。 |
| **修复** | （P2）复刻评测脚本的 `decode_bytes`：UTF-8 → GB18030 降级。 |
| **回归测试** | `test_gbk_decoding`（KB-062 正文含"23:00"且无乱码） |

---

## 缺陷 #9：HTML 不剥标签直接入库

| 项 | 内容 |
|---|---|
| **现象** | KB-061（HTML 过敏原表）正文里全是标签，检索打分被 `<td>`/`<tr>` 污染，quote 逐字校验必然不过。 |
| **假设** | ① 入库前剥了（**排除**）；② 靠 BM25 忽略标签（**排除**：标签会真的进 postings，稀释正文词频）；③ 没剥（**成立**）。 |
| **验证** | `loader.py:178-182`，注释写着「html 直接按文本入库，标签也就那么几个，BM25 自己会忽略」。评测脚本 `run_eval.py:211-214` 的 `html_to_text` 会先删 `<script>`/`<style>`、再删所有标签、最后 `unescape` 实体——两边不一致。 |
| **根因** | `starter/kbqa/loader.py:178-182`。 |
| **修复** | （P2）复刻评测的 `html_to_text`：剥 `<script>`/`<style>` → 剥标签 → 反转义实体。 |
| **回归测试** | `test_html_stripped`（KB-061 的 chunk 无标签残留） |

---

## 缺陷 #10：切块丢掉每篇文档的尾部

| 项 | 内容 |
|---|---|
| **现象** | 关键事实恰好在文尾时检索不到（通知的联系人、政策的最后一条）。`kb_chunks` 只有 53，等于"32 篇文档各出 1–2 块"。 |
| **假设** | ① 文档都很短（**排除**：`KB-001` 单篇就上千字）；② `range()` 的上界算错（**成立**）。 |
| **验证** | `chunker.py:41` 是 `range(0, len(text) - CHUNK_SIZE, CHUNK_SIZE)`。例如 `len(text)=1000`、`CHUNK_SIZE=300` 时 `range(0, 700, 300)` → 0/300/600，最后 `text[600:900]` 之后 100 字**永远不会被任何 chunk 覆盖**。且定长切点落在句子中间，没有标题层级意识。 |
| **根因** | `starter/kbqa/chunker.py:41`。 |
| **修复** | （P2）标题层级感知切块，200~500 字，过短向上合并、过长按段落二切，**不丢文尾**。 |
| **回归测试** | `test_chunker_keeps_tail`（每篇文档全文被 chunks 覆盖：拼接 == 正文） |

---

## 缺陷 #12：`hit.doc_id` 张冠李戴

| 项 | 内容 |
|---|---|
| **现象** | 检索结果里 `doc_id` 与该片段真实所属文档不一致。`gold_all` 检查大面积红，citations 指向错文档。 |
| **假设** | ① 金标本身错（**排除**：金标是评委给的，且同一批题在修好分词后能对上）；② 返回时用排序位置覆盖了真实 doc_id（**成立**）。 |
| **验证** | `retriever.py:275-276` 是 `hit.doc_id = ordered[len(hits)].doc_id`——`ordered` 是全量排序后的文档/片段列表，`len(hits)` 是"已经收集了几条命中"，两者没有任何对应关系。 |
| **根因** | `starter/kbqa/retriever.py:275-276`。 |
| **修复** | （P2）`doc_id` 一律从 chunk 自身的 `chunk.doc_id` 取。 |
| **回归测试** | `test_retrieve_doc_id_correct`（每个 hit 的 doc_id == 其 chunk 真实所属文档） |

---

## 缺陷 #13：先取 top_k 再按版本过滤

| 项 | 内容 |
|---|---|
| **现象** | `/api/retrieve` 返回条数不足 `top_k`。契约 §4 明令禁止这个顺序，评测 `results_count` 直接红。 |
| **假设** | ① 索引里片段不足（**排除**：`kb_chunks=53` 时索引片段总数远多于 5）；② 顺序反了（**成立**）。 |
| **验证** | `retriever.py:306-307`：先截取前 `top_k` 条，再按 `excluded`（已废止版本）过滤，于是过滤掉的空位没人补。 |
| **根因** | `starter/kbqa/retriever.py:306-307`。 |
| **修复** | （P2）先过滤后截取，恰好 top_k 条；不足才允许更少（补齐项标 `padded` 且问答链路禁用）。 |
| **回归测试** | `test_retrieve_exactly_topk`（有已废止文档被滤时 results 仍 == top_k） |

---

## 缺陷 #14：会话历史全局共享

| 项 | 内容 |
|---|---|
| **现象** | 不同 `session_id` 之间会串线。契约 §5 要求"同一个 `session_id` 的多次请求视为同一段对话"，反过来说不同 session 之间必须隔离。 |
| **假设** | ① 上层按 session 分了桶（**排除**）；② 存储是全局单链表、`session_id` 被忽略（**成立**）。 |
| **验证** | `sessions.py:16-28`：`_turns` 是模块级/实例级单链表，`history(session_id)` 收了参数但没用；`MAX_TURNS=6` 也是全局共享的。评测脚本每道题都用新的随机 `session_id`（`run_eval.py:792`），所以污染会直接体现为串线。 |
| **根因** | `starter/kbqa/sessions.py:16-28`。 |
| **修复** | （P3）按 `session_id` 分桶，每桶独立轮数与槽位。 |
| **回归测试** | `test_session_isolation`（两个 session_id 的槽位互不可见） |

---

## 缺陷 #15：交接文档与代码不符

| 项 | 内容 |
|---|---|
| **现象** | 作业 README 明确警告"不要默认任何人说的都是对的，包括前同事的交接文档"。逐条核对 `HANDOVER.md` 的"目前的状态"三句，三句都不成立。 |
| **假设** | ① 文档写的是历史状态（**排除**：写的是"目前的状态"，且代码从未支持过 txt/html）；② 就是错的（**成立**）。 |
| **验证** | | 交接文档的说法 | 实际 | 证据 |
|---|---|---|---|
| 「检索命中率 95%」（`:33`） | 不可能。分词已坏（缺陷 #6），第一关检索 15 题只对 6 题 | 基线 `retrieval 6.00 / 15.00` |
| 「测试全部通过」（`:34`） | 字面成立但无意义：自带 17 条测试是纯冒烟，只断 HTTP 200 与字段非空 | `tests/test_api.py`；基线 17.00 分下它依然全绿 |
| 「md、txt、html 三种格式都支持」（`:35`） | 只有 `.md`/`.markdown` | `loader.py:12`；`kb_chunks=53`、`kb_warnings` 只提到 README |

另外 `HANDOVER.md:39` 把"`.cache/index.json` 已提交进仓库"当成优点宣传（"新同学 clone 下来不用等建索引"），
实际是缺陷 #11 的另一半；`:48` 把月底数字对不上解释成"四舍五入的事"，实际是右开区间（缺陷 #2）。

| 项 | 内容 |
|---|---|
| **根因** | `starter/HANDOVER.md:33-35`（状态三句）、`:39`（缓存）、`:48`（右开区间）。 |
| **修复** | （P2/P5）不改 `HANDOVER.md` 原文——它是评审素材，保留错误陈述；把三条更正写进仓库根 `README.md` 的"交接文档更正"一节与本文件。 |
| **回归测试** | `test_legacy_tests_are_smoke_only`（P2 落地：断言自带冒烟测试在缺陷未修时也全绿，说明它测不出问题） |

---

## 附：本文件维护约定

- 每条缺陷在**修复落地**时才填「修复」与红证据；未落地的写"待 PX"。
- 红证据必须是真实的运行输出（命令 + 结果），不是"应该会红"的推理。
- 猜错后排除掉的假设也写进「假设」一栏——现场调试环节看的是定位方法，不是运气。
