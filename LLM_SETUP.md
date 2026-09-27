# LLM 接入说明

> 模板依据：`docs/API_CONTRACT.md` §7.4（八节）。
> **本文档的状态：P0 骨架。** 第 1–6 与第 8 节记录的是 starter 现状与我的接入计划；
> 第 7 节在 P3 跑完 `eval/llm_gateway.py preflight` 后回填真实输出。
> 每一节最后都标注了它会在哪个阶段被"写完"。

---

## 1. 用了什么

| 项 | 值 | 状态 |
|---|---|---|
| 厂商 | DeepSeek（`https://api.deepseek.com`） | **已接入并实测**（2026-09-26，见 §7.4） |
| 模型名 | `deepseek-flash`（DeepSeek-V4.1-Flash，`GET /models` 实测确认存在） | **已接入并实测** |
| 协议 | OpenAI 兼容 Chat Completions（`POST {LLM_BASE_URL}/chat/completions`） | 已实测 |
| SDK | 不用 SDK，直接用 `httpx`（starter 现有依赖） | 现状 |
| 为什么这么选 | 契约 §7.1 推荐的路线；评审时评委就是用这一套切。不用官方 SDK 是为了把"地址原样拼接"这条握在自己手里（SDK 的 `base_url` 处理方式在不同版本间变过） | — |

现有实现位置：`starter/kbqa/llm.py`（`LLMClient.chat` / `chat_with_retry`）。
**P3 会按契约 §7.3 复核并补齐**：思考模式（`reasoning_content` 原样回传）、
`max_tokens`、异常 `finish_reason`、错误码、工具调用多轮循环、空行与 keep-alive、
180 秒总预算与单次超时。

---

## 2. 配置从哪里读

全部走**环境变量**，没有配置文件、没有命令行参数。

| 变量 | 含义 | 默认值 | 读取位置 |
|---|---|---|---|
| `LLM_BASE_URL` | 模型服务地址，**原样拼接** `/chat/completions` | `""`（空） | `config.py:71` → `Settings.llm_base_url` |
| `LLM_API_KEY` | 模型 Key，以 `Authorization: Bearer` 发送 | `""`（空） | `config.py:72` → `Settings.llm_api_key` |
| `LLM_MODEL` | 模型名 | `""`（空） | `config.py:73` → `Settings.llm_model` |
| `LLM_TIMEOUT` | 单次模型调用超时（秒） | `120` | `config.py:75` |
| `CHAT_BUDGET` | 一次 `/api/chat` 的总预算（秒） | `150` | `config.py:77` |

三个 `LLM_*` 变量**必须同时非空**才进入 `live` 模式（`config.py:56` 的 `Settings.live`），
否则 `mock` 降级。地址只做 `strip()` 和去掉尾部 `/`，**不补 `/v1`、不截路径、不只取域名**
（`config.py:71`），所以带路径前缀的代理也能接上。

`CHAT_BUDGET` 默认 150 秒而不是契约的 180 秒，是为了给响应序列化和网络留余量。
**P3 复核后如需贴满 180 秒会调整**，并在 README 说明。

其他相关变量（与模型无关，但影响重建）：`DATA_DIR`、`KB_DIR`、`VAR_DIR`、`TODAY`。

---

## 3. 怎么换成你们的

一步一步，只改环境变量，**不改代码**：

```bash
# 1) 停掉当前服务

# 2) 用你们的三个值重启服务（POSIX 用 .venv/bin/python）
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=<你们的 Key>
export LLM_MODEL=deepseek-flash
cd starter
.venv/Scripts/python -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000
```

- **要不要重建？** 不需要。索引与清洗表跟大模型无关，换模型不用重跑 `make rebuild`。
- **要不要重启？** 需要。配置在进程启动时读一次（`load_settings()`），没有热加载。
- **怎么确认切过去了？** `curl http://localhost:8000/api/health`，`llm_mode` 应为 `"live"`。
- **走代理（契约 §7.5）？** 把 `LLM_BASE_URL` 设成代理打印出来的地址即可，不要加 `/v1`。

Windows PowerShell 写法：

```powershell
$env:LLM_BASE_URL="https://api.deepseek.com"; $env:LLM_API_KEY="<Key>"; $env:LLM_MODEL="deepseek-flash"
.\starter\.venv\Scripts\python.exe -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000
```

---

## 4. 怎么看到发给模型的请求

契约 §7.2.2（可观察）有两条路，**两条都会做到**：

### 4.1 走 HTTP 代理（评审时用的就是这条）

服务只发 HTTP，所以把地址指向评委的代理就行，**不需要改我的代码**：

```bash
python eval/llm_gateway.py proxy --upstream https://api.deepseek.com --log llm_traffic.jsonl
# 用它打印出来的地址作为 LLM_BASE_URL 重启服务，之后每次请求/响应都落进 llm_traffic.jsonl
```

### 4.2 服务自己的留痕开关

每次调用都会把请求与响应写进 trace（`/api/trace/{trace_id}`），**默认开启，没有开关**：

| trace 字段 | 内容 |
|---|---|
| `llm[].endpoint` | 实际请求的完整地址 |
| `llm[].model` | 实际发送的 `model` 值 |
| `llm[].prompt` | 发给模型的 messages 原文 |
| `llm[].tools` | 工具定义条数 |
| `llm[].finish_reason` / `status` / `usage` | 响应元信息 |
| `llm[].raw_content` / `raw_reasoning` | 模型原始输出与思考内容 |
| `llm[].took_ms` / `error` / `detail` | 耗时与真实失败原因 |

live 模式还多了一层**事实溯源**（泛化 R2 引入，`steps` 数组里按发生顺序）：

| trace `step` | 内容 |
|---|---|
| `tool` | 工具名、**实际执行的参数**与模型**原本提议**的参数（`proposed`，R3 起） |
| `tool_scope_normalized` | PlanToolPolicy 按 Plan 补齐了哪些参数（`status=filled` / `filled` / `effective`）（R3 起） |
| `tool_scope_rejected` | 模型参数与已解析范围冲突 → **拒绝执行**，含 `field` / `expected` / `received` / `message`（R3 起） |
| `tool_receipt_created` | `receipt_id` / `tool` / `params` / `numbers` / `result` 摘要 —— **模型看到的 canonical 事实** |
| `final_validation` | 校验是否通过、哪些数字没有依据、回答里有几个数字 |
| `repair_attempt` | 一次有界 repair 的结果（`valid` / `still_unsupported` / `no_budget` / `llm_error`） |
| `evidence_selected` | 最终选进 `data_evidence` 的 receipt 集合（回答用了哪几条事实） |
| `evidence_projected` | 投影后的数字总数与每条字节数（对齐评测 `evidence_hygiene`） |

`trace["plan"]`（R3 起）就是那份 **canonical Plan**：`as_trace()` 额外带出
`provenance`（`store` / `product` / `window` / `metric` 各自是
`explicit` / `inherited` / `derived` / `default` / `none`），用来在调试面板里回答
"这个门店是用户说的、还是上一轮继承的、还是根本没解析出来"。参考 `DEMO.md` §4。

R5 的跨用户 turn 请求形状已经改变：当前模型请求包含本轮 Plan 与结构化
`ConversationState`，不再追加前 3 轮的 user/assistant 自然语言消息。这样上一轮
assistant 的数字不会成为当前事实来源；但同一个 `/api/chat` 内，模型发出的 assistant
tool-call 消息、`reasoning_content` 和随后回传的 `role=tool` 消息仍按 DeepSeek 协议原样
保留。R5 preflight P13 已验证 18 次多轮请求的 `reasoning_content` 回传。

于是"模型为什么知道这个数字"可以在 trace 里一条线看下来：

```text
tool_receipt_created  D1 query_metrics(2026-07-01..07-31) → qty=73
final_validation      pass=true
evidence_selected     receipts=["D1"]
```

**关键区别**：模型读到的 `role=tool` 内容是 `model_projection`（完整事实，必要时结构化收缩，
绝不出 `truncated` stub）；`data_evidence` 是另一条 `evidence_projection`（受 4096 字节 /
60 数字约束）。两者都由同一条 receipt 派生，所以"模型看到的"与"落库的"永远同源。

脱敏样例（Key 从不入 trace，`Authorization` 头不记录）：

```json
{"endpoint": "http://127.0.0.1:8900/chat/completions", "model": "deepseek-flash",
 "messages": 3, "tools": 11, "prompt": "[{\"role\": \"system\", \"content\": \"…\"}]",
 "status": 200, "finish_reason": "stop", "content_chars": 148,
 "has_reasoning": true, "took_ms": 3120.4}
```

> **已知不足（P3 修）**：`llm.py:184-186` 的 `_preview()` 把 prompt 与 raw_content
> **截断到 4000 字**，而契约 §6 要求"完整提示词和模型原始输出"。P3 会改成完整留存
> （体积大时写文件、trace 里存路径）。这一条写在《8. 已知限制》里。

---

## 5. 没有 Key 时会怎样

**服务照常启动**，四个接口都正常（这是契约 §7.2.3 的硬要求）：

| 接口 | 无 Key 时的行为 |
|---|---|
| `GET /api/health` | 200，`llm_mode` 为 `"mock"` |
| `GET /api/metrics/summary` / `daily` | 200，与是否有 Key 无关 |
| `POST /api/retrieve` | 200，检索完全不依赖大模型 |
| `POST /api/chat` | 200，走 `Answerer` 的规则模板回答（`service.py:177`），**不返回 500** |

降级策略：`service.py:176-181`——`not settings.live` 时直接走 `Answerer`，
不构造 `LLMClient`、不发任何网络请求。启动时**不校验 Key 的格式、不查余额、不列模型**
（契约 §7.2 明确禁止，因为评委换配置时最容易在这种检查上翻车）。

基线实测：无 Key 下 55 题全部拿到 HTTP 200 + 合法 JSON，得分 17.00/100
（见 `EVAL_REPORT.md` §0）——**降级模式不是"接口挂了"，是"答得差"**。

---

## 6. 依赖与安装

| 项 | 值 |
|---|---|
| 额外依赖 | **无**。用现有 `httpx`，不装 OpenAI/Anthropic SDK |
| 模型文件下载 | **无**（问答链路不下载本地模型） |
| 首次启动耗时 | 索引加载约 1 秒内（35 篇文档）；清洗表已存在时跳过重建 |
| Python | 3.12 |
| 依赖清单 | `starter/requirements.txt`：`fastapi`、`uvicorn`、`httpx`、`pytest` |

> 向量检索（契约 §7.6，可选）如果 P2 接入本地模型，会在这里补上"首次下载体积、
> 存放位置、离线时的降级行为"，并在 README 写明大小。**当前计划：先只上 BM25，
> 向量作为 P2 的可选增强**；向量不可用时 `/api/retrieve` 必须退回 BM25 且不 500。

---

## 7. 自测结果

### 7.1 `preflight` 输出（**P1–P14 全部通过**）

运行命令（本机没有可用的真实 Key，所以用 `eval/llm_gateway.py` 自带的假模型服务；
它按 DeepSeek 文档行为模拟，不花钱、不需要 Key）：

```bash
# 终端 A：起你的服务，环境变量指向假模型
cd starter
LLM_BASE_URL=http://127.0.0.1:8901/ds-gw \
LLM_API_KEY=preflight-key-3b9c1f \
LLM_MODEL=preflight-model-7f3a \
.venv/Scripts/python -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000

# 终端 B
python eval/llm_gateway.py preflight --service-url http://localhost:8000 \
    --port 8901 --model preflight-model-7f3a --api-key preflight-key-3b9c1f \
    --prefix /ds-gw
```

实测输出（完整原文见 `eval/_preflight/preflight_report.md`）：

```
编号  检查项                                                            结果  说明
----------------------------------------------------------------------------------
P1    服务确实把请求发到了注入的 LLM_BASE_URL（含路径前缀）             通过  共观察到 60 次 POST /ds-gw/chat/completions。
P2    请求里的 model 等于注入的 LLM_MODEL                               通过  全部请求都用了 preflight-model-7f3a。
P3    注入的 Key 以 Authorization: Bearer 发送                          通过  全部请求都带了正确的 Bearer Key。
P4    只用了 DeepSeek 文档列出的顶层参数                                通过  只出现了 DeepSeek 文档列出的顶层参数。
P5    max_tokens 不设，或不小于 2048                                    通过  max_tokens 都不小于 2048。
P6    没有访问 {prefix}/chat/completions 之外的任何路径                 通过  只访问了 POST /ds-gw/chat/completions。
P7    工具定义规范，且每一个工具调用都以 role=tool + tool_call_id 回传  通过  44 个工具调用的结果都正确回传了。
P8    每个场景下 /api/chat 都返回 HTTP 200 与字段完整的合法 JSON        通过  32 次问答全部返回 200 和字段完整的 JSON。
P9    模型不可用时给出结构化 refusal，answer 从不是空串                 通过  模型不可用的场景下都给了结构化 refusal。
P10   思考内容没有漏进 answer / citations / data_evidence               通过  32 次回答里，思考标记都没出现在任何对外字段里。
P11   /api/chat 在时限内返回（含长时间无响应的场景）                    通过  最慢的一次是 23.55 秒，都在 180 秒以内。
P12   注入环境变量后 /api/health 报告 llm_mode = live                   通过  llm_mode = live。
P13   多轮工具调用之间 reasoning_content 原样回传（没有触发 400）       通过  18 次多轮请求都原样回传了 reasoning_content。
P14   保持连接的空行与 SSE 注释没有把服务弄坏                           通过  slow 场景照常给出回答。

预检通过：在 OpenAI 兼容这条路线上，我们能原样接上你的服务。
```

**两条必须说明的事（否则这份输出会被误读）：**

1. **假模型的 `model` 名与 Key 是我们自己指定的**，不是 DeepSeek 的真实值。
   预检检查的是"服务有没有原样用注入的值"，不是"值对不对"。
   换成你们的三件套不需要改代码——见第 3 节。
2. **本机没有可用的真实 DeepSeek Key**，所以这份输出是**假模型**下的结果，
   不是真实模型下的作答质量。这一点在 `EVAL_REPORT.md` §3 里也写明了：
   最终得分是 **无 Key 的 mock 降级模式**跑出来的（88.00/100）。

### 7.2 preflight 修掉的三处（以及一处踩坑说明）

| 检查项 | 原来为什么不过 | 怎么修的 |
|---|---|---|
| **P1 / P12** | 服务没按注入的 `LLM_BASE_URL` 发请求，或者没重启 | 不是代码问题：`LLM_BASE_URL` 必须**原样含路径前缀**转发（`config.py` 只做 `strip` 和去尾斜杠，不补 `/v1`、不截路径），且改完要**重启**服务 |
| **P9** | `finish_reason` 为 `length`/`content_filter`/`insufficient_system_resource`/`aborted` 时，以及"没有 `tool_calls` 而 `content` 为空"时，被当成正常回答 | 这四类一律按错误处理 → 结构化 refusal；`live` 模式的异常**不再回退到本地模板回答**（回退会让 `answer_type` 变成 `data`，P9 就是因为这个判红） |
| **P14 / P13** | — | 本来就对：空行与 `: keep-alive` 注释走 `_strip_noise()` 跳过；assistant 消息**整条原样回传**（含 `reasoning_content`） |

> **踩坑记录（现场调试会问）**：第一次跑 preflight 时 P1 红而 P12 绿，
> 看起来自相矛盾。原因是**预检自己的假模型没能绑上端口**——我先手动起了一个假模型
> 占着 8901，预检再起一个时绑定失败，但它的提示信息照打"假模型已启动"。
> 于是预检统计到的请求数是 0，而我的服务确实在往那个端口发请求。
> 怎么确认的：**把假模型停掉再问一次**，服务返回了
> "模型服务这次没有正常返回（接口返回错误码 502）"——这条 refusal 反证了
> 服务真的在调它。把端口腾出来重跑，P1 立刻通过。
> 教训：**"两项表现矛盾"时先怀疑观测手段，不要先怀疑被测对象。**

### 7.3 异常三类怎么验证（契约 §7.4 第 7 节）

| 异常 | 怎么造 | 实测结果 |
|---|---|---|
| 空回答 | `fake --scenario empty_content` / `json_empty` | `/api/chat` 返回 200 + `answer_type=refusal`，trace 记 `empty_content` |
| 超时 | `fake --scenario hang`（连接建立但永不响应） | read timeout 独立于 connect timeout，到点返回结构化 refusal，不挂起（P11 实测最慢 23.55 秒） |
| 报错 | `fake --scenario http_401/402/422/429/500/503` | 每种都 200 + refusal，trace 记真实状态码；429/500/503 先重试一次 |

---

### 7.4 真实 Key 下的接入验证与全量评测（2026-09-26）

拿到真实 DeepSeek Key 后，按第 3 节的步骤（只改环境变量、零代码改动）切换并全量评测：

```bash
# 1) 停掉 mock 模式的旧服务
# 2) 三个环境变量重启服务（Windows Git Bash 写法；PowerHELL 见第 3 节）
cd starter
LLM_BASE_URL=https://api.deepseek.com \
LLM_API_KEY=<真实 Key> \
LLM_MODEL=deepseek-flash \
.venv/Scripts/python -m uvicorn kbqa.server:app --host 127.0.0.1 --port 8000

# 3) 确认切换生效
curl http://localhost:8000/api/health        # "llm_mode": "live"

# 4) 全量评测
python eval/run_eval.py --base-url http://localhost:8000 \
    --questions eval/public_questions.jsonl --out eval/_live_raw
```

实测记录（代码 commit `6301aea`）：

| 检查 | 结果 |
|---|---|
| `GET /models` 能列出 `deepseek-flash` | 通过（DeepSeek-V4.1-Flash，1M 上下文，思考模式默认开启） |
| `/api/health` → `llm_mode=live` | 通过 |
| 冒烟：数据题「S02 6 月净营业额」 | 通过：真实查询 43,655.00 元，`data_evidence` 带完整 query_metrics 结果 |
| 冒烟：文档题「外卖订单多久内可退款」 | 通过：引用现行版本 KB-013（退款政策 v2）——版本时效在真实模型下正常 |
| 公开题库 55 题 | 首评 **87.50 / 100**（代码 `6301aea`，失分三类根因见 `EVAL_REPORT.md` §6.1 与 `DEBUG_LOG.md` #27–#29） |
| 自补题库 12 题 | 首评 **26.00 / 28.00** |
| P6 修复后复评 | 公开题库 **100.00 / 100**、自补题库 **28.00 / 28**（代码 `ddd259f`，修复链与逐题对照见 `EVAL_REPORT.md` §6.2；报告原件 `eval/_live_p6_final/`） |
| Key 是否入库/进 trace | 否——只通过环境变量注入进程；`tests/test_no_secrets.py` 保持全绿 |

**换真实模型不需要改任何代码**——第 3 节的步骤原样走通，这正是预检 P1–P14
想保证的事。首评暴露的六个作答层回归（证据尺寸 / 估算数字纪律 / 工具循环上限 /
BM25 查询用法 / 无解释引用 / as-of 语境）与协议无关，已作为 P6 全部修复，
记录在 `DEBUG_LOG.md` #27–#32。

---

### 7.5 泛化 R2 后的复跑（2026-09-27）

泛化 R2 重写了 live 作答权威（ToolReceipt / FactLedger / Finalisation Authority），
改的是**作答层**，不碰协议层。按任务书要求必须复跑预检，确认 DeepSeek 兼容性、
`reasoning_content` 回传、工具调用语义、超时、HTTP 错误降级、无 Key 行为**不退化**。

命令（无 Key，用本地假模型顶替 DeepSeek）：

```bash
# 先用注入的三个环境变量把服务起在 8000（此时 LLM_BASE_URL 指向假模型 18801）
cd starter
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
  .venv/Scripts/python ../eval/llm_gateway.py preflight \
  --service-url http://localhost:8000 \
  --port 18801 --no-wait
```

实测结论：**P1–P14 全部通过**（`exit=0`），14 项无误。报告原件
`eval/_preflight/preflight_report.md`。关键几项：

| 编号 | 检查项 | 结果 | 实测说明 |
|---|---|---|---|
| P1 | 请求确实发到注入的 `LLM_BASE_URL`（含路径前缀） | 通过 | 共观察到 60 次 `POST /ds-gw/chat/completions`。 |
| P7 | 工具定义规范，且每个工具调用以 `role=tool` + `tool_call_id` 回传 | 通过 | 44 个工具调用的结果都正确回传。 |
| P8 | 每个场景 `/api/chat` 返回 200 与字段完整 JSON | 通过 | 32 次问答全部 200 + 字段完整。 |
| P11 | 在时限内返回（含长时无响应场景） | 通过 | 最慢 121.38 秒（`hang` 场景 read 超时），都在 180 秒以内。 |
| P13 | 多轮之间 `reasoning_content` 原样回传（未触发 400） | 通过 | 18 次多轮请求都原样回传了 `reasoning_content`。 |

与 R1（§7.1）逐项对比**无退化**：空回答 / 超时 / 各档 HTTP 错误码仍全部降级为
结构化 `refusal`（`answer_type=refusal`，`answer` 从不是空串），
思考标记仍只进 trace、不进 `answer`/`citations`/`data_evidence`。R2 新增的
Finalisation Authority 只在**正常作答路径**生效，模型不可用时的降级路径未变。

**两次现场踩坑（记录在此，免得下次再撞）：**

1. **Git Bash 会改写命令行里以 `/` 开头的选项值。** 一旦显式传 `--prefix /ds-gw`，
   MSYS 会把它转成 `C:/Users/.../ds-gw`，于是 P1/P6 判定请求打歪、失败。
   默认值不受影响，但为避免踩坑，整条命令加 `MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'`
   前缀；PowerShell 无此问题。
2. **默认端口可能被 Windows 保留。** 预检默认的 8901 绑定时报 `WinError 10013`，
   `netsh int ipv4 show excludedportrange tcp` 显示 8889–8988 整段被 Hyper-V/系统
   排除。改到 **18801** 后正常。另外 `hang` 场景会让脚本在退出前等待，加
   `--no-wait` 避免卡在交互式确认上。

---

### 7.6 泛化 R3 后的复跑（2026-09-27）

泛化 R3 收敛的是**规划权威**，并且第一次把**结构化 Plan 下传进 system 消息**
（`Plan.as_model_context()` 以 JSON 追加进同一条 system，见 `kbqa/live.py` 的
`PLAN_CONTEXT_HEADER`）。这一改动直接落在"发给模型的请求"上，所以必须复跑预检，
确认三件事不退化：**只用了文档列出的顶层参数**（P4）、**思考内容不外泄**（P10）、
**工具调用仍以 `role=tool` + `tool_call_id` 正确回传**（P7）。

本轮把预检包成一条命令（`eval/preflight_driver.py`：起假模型 → 用注入的三个环境变量
自动重启服务 → 检查 → 写报告），省掉"两个终端互相等回车"：

```bash
# 从仓库根目录；Git Bash 需加 MSYS_NO_PATHCONV=1（见 §7.5 坑 1）
MSYS_NO_PATHCONV=1 starter/.venv/Scripts/python eval/preflight_driver.py
```

实测结论：**P1–P14 全部通过**（输出末尾 `PREFLIGHT_PASSED=True`）。报告原件
`eval/_r3_preflight/preflight_report.md`。关键几项：

| 编号 | 检查项 | 结果 | 实测说明 |
|---|---|---|---|
| P1 | 请求确实发到注入的 `LLM_BASE_URL`（含路径前缀） | 通过 | 共观察到 60 次 `POST /ds-gw/chat/completions`。 |
| P4 | 只用了 DeepSeek 文档列出的顶层参数 | 通过 | **plan context 只是往 `messages[0].content` 里追加文本，没有新增任何请求参数**。 |
| P7 | 工具定义规范，且每个工具调用以 `role=tool` + `tool_call_id` 回传 | 通过 | 44 个工具调用的结果都正确回传（含 PlanToolPolicy 拒绝时返回的结构化错误消息）。 |
| P8 | 每个场景 `/api/chat` 返回 200 与字段完整 JSON | 通过 | 32 次问答全部 200 + 字段完整。 |
| P10 | 思考内容没有漏进 `answer` / `citations` / `data_evidence` | 通过 | 32 次回答里，思考标记都没有出现在任何对外字段里。 |
| P11 | 在时限内返回（含长时无响应场景） | 通过 | 最慢 123.50 秒（`hang` 场景 read 超时），都在 180 秒以内。 |
| P13 | 多轮之间 `reasoning_content` 原样回传（未触发 400） | 通过 | 18 次多轮请求都原样回传了 `reasoning_content`。 |

与 R2（§7.5）逐项对比**无退化**：空回答 / 超时 / 各档 HTTP 错误码仍全部降级为
结构化 `refusal`（`answer_type=refusal`，`answer` 从不是空串）。

**本轮踩坑（记录在此，免得下次再撞）：** 把"自动重启服务"写进复跑脚本后，
脚本用 `netstat -ano` 找占端口的 PID，但 `subprocess.run(..., text=True)` 在
中文 Windows 上按 GBK 解码、遇到 netstat 里的非 UTF-8 字节会在读线程里抛
`UnicodeDecodeError`，于是 `stdout` 变成 `None`、脚本在 `kill_port()` 上崩掉
（而假模型已经起来了，看起来像"预检自己坏了"）。改成
`encoding="utf-8", errors="replace"` 即可。**"预检红了"先看脚本自己的输出解码，
再看被测服务**——与 §7.2 那条踩坑同一个教训。

### 7.7 泛化 R4 后的复跑（2026-09-27）

泛化 R4 改的是**检索信任边界**：`search_kb` 现在把 Plan 的**结构化范围**
（`as_of/store_id/historical/window/year`）注入检索，并且**进模型的文本是安全投影**
（`source_text`/`source_dropped` 被 `FactLedger.model_projection` 摘掉）。它落在
"发给模型的请求"上（`role=tool` 的内容形状变了），必须复跑预检确认三件事不退化：
**只用了文档列出的顶层参数**（P4，范围是注入进 `params` 不是新增请求参数）、
**思考内容不外泄**（P10）、**工具调用仍以 `role=tool` + `tool_call_id` 正确回传**（P7）。

本轮沿用 R3 的一条命令模式（自己的输出目录，避免覆盖 R3 证据）：

```bash
# 从仓库根目录；Git Bash 需加 MSYS_NO_PATHCONV=1（见 §7.5 坑 1）
MSYS_NO_PATHCONV=1 starter/.venv/Scripts/python eval/preflight_driver_r4.py
```

实测结论：**P1–P14 全部通过**（输出末尾 `PREFLIGHT_PASSED=True`）。报告原件
`eval/_r4_preflight/preflight_report.md`。关键几项：

| 编号 | 检查项 | 结果 | 实测说明 |
|---|---|---|---|
| P1 | 请求确实发到注入的 `LLM_BASE_URL`（含路径前缀） | 通过 | 共观察到 60 次 `POST /ds-gw/chat/completions`。 |
| P4 | 只用了 DeepSeek 文档列出的顶层参数 | 通过 | **结构化检索范围注入的是工具 `params`（`role=tool`），没有新增任何请求参数**。 |
| P7 | 工具定义规范，且每个工具调用以 `role=tool` + `tool_call_id` 回传 | 通过 | 44 个工具调用的结果都正确回传（含安全投影后的知识结果）。 |
| P8 | 每个场景 `/api/chat` 返回 200 与字段完整 JSON | 通过 | 32 次问答全部 200 + 字段完整。 |
| P10 | 思考内容没有漏进 `answer` / `citations` / `data_evidence` | 通过 | 32 次回答里，思考标记都没有出现在任何对外字段里。 |
| P11 | 在时限内返回（含长时无响应场景） | 通过 | 最慢 123.59 秒（`hang` 场景 read 超时），都在 180 秒以内。 |
| P13 | 多轮之间 `reasoning_content` 原样回传（未触发 400） | 通过 | 18 次多轮请求都原样回传了 `reasoning_content`。 |

与 R3（§7.6）逐项对比**无退化**。本轮无新增踩坑（R3 记下的两个坑——Windows 保留端口、
Git Bash 改写 `/ds-gw` 前缀——在 `preflight_driver_r4.py` 里已经固化：端口用 18801，
脚本里自带 `--no-wait`，调用方加 `MSYS_NO_PATHCONV=1`）。

---

## 8. 已知限制
清楚但还没解决的，一并写在这里。

1. **真实 Key 已接入（2026-09-26），live 终评 100.00 / 100 + 28.00 / 28**（`EVAL_REPORT.md` §6）。
   首评 87.50 暴露的六个失分根因已全部定位并修复（`DEBUG_LOG.md` #27–#32，红测试先提交）：
   live 引擎的 `data_evidence` 不做尺寸收口（4 题，回答本身全对）；
   估算数字纪律未传导给模型（H02，把"大概 150 份"写进了解释）；
   工具循环 4 轮上限对"检索不到就换词再试"的真实模型偏低（H06/T02-3）；
   长查询在 BM25 里稀释强信号词（C04）；明说"没有找到"仍挂他店引用（H06）；
   as-of 追问的语境没传给模型（V03）。修复全部落在传导层（提示词/收口/闸门），
   评分逻辑与评测判据一行未动，mock 基线 234 passed 无回退。
2. **流式输出还没做**（契约 §7.3 最后一行）。`/api/chat` 目前是非流式，
   `delta.reasoning_content` → `delta.content` 的顺序处理、前端"思考中"状态都在 P4。
   预检里与流式有关的项会显示"未检查"而不是"通过"。
3. **`CHAT_BUDGET` 默认 150 秒**，不是契约的 180 秒上限，留了 30 秒给响应序列化。
   预检 P11 实测最慢 23.55 秒（含 `hang` 场景的 read 超时），离上限很远。
4. **live 模式下模型回答里的数字会被校验，但校验器不再"重答"**（泛化 R2 改动）。
   凡涉及经营数字，必须能在工具结果或引用摘句里找到依据；找不到就**只**让模型
   基于已有事实**改写一次**（repair 轮不带工具、不新增事实），仍不通过则返回
   结构化 refusal。旧行为（校验失败 → 交给另一套 `Answerer` 重新回答）已删除——
   它会把"模型已经答对的问题"重新答错（H069，见 `AI_USAGE.md` 2.24 与
   `DEBUG_LOG.md` #40）。数字口径与评测脚本一致（`kbqa/core/numbers.py`）。
5. **检索信任边界已闭合（泛化 R4）。** `search_kb` 给模型的 `role=tool` 内容
   现在是**安全投影**：`text` 是 sanitize 后的安全文本（通用注入识别 + 反向护栏，
   正经公司制度不被误删）；原始来源 `source_text` / 剥掉的指令句 `source_dropped`
   放在 receipt 里、由 `FactLedger.model_projection` 摘掉，**永不进模型上下文**。
   引用由 `kbqa/citations.py` 从**本轮真的检索到的 chunk**生成（逐字、规范化 ≤400、
   跳过被 sanitize 的指令句、拒绝未见文档）。公开接口 `Service.retrieve()`
   **仍回原文**（evaluator 逐字校验对的是它）——两个接口目标相反，是刻意分开的两条路。
   缺陷与证据见 `DEBUG_LOG.md` #52–#59。
6. **思考模式开关未显式设置**（`deepseek-flash` 默认开启），理由见 README：
   多轮工具调用更稳，代价是更慢更贵；`reasoning_content` 只进 trace，不进 answer。
7. **`max_tokens` 显式 4096**（契约要求不设或不小于 2048）。
8. **`tool_choice: "auto"` 是唯一带上的非必需参数**，预检 P4 判定它在
   DeepSeek 文档列出的顶层参数之内。若你们那边有异议，删掉它不影响功能。
9. **trace 里超过 256KB 的字段会写文件、trace 存路径**（`var/llm_payloads/`）。
   正常一轮提示词只有几 KB，不会触发。
10. **工具范围策略只覆盖"取数范围"，`search_kb` 的检索词仍由模型组织**（泛化 R3/R4）。
    Planner 返回的 Plan 是这一轮的语义终态，`PlanToolPolicy` 会拒绝与它冲突的**取数**调用；
    而 `search_kb` 的**检索词**仍由模型自由组织——但它的**版本/时点/门店范围**现在由
    应用从 Plan **结构化注入**（`as_of/store_id/historical/window/year`）。
    这些字段**不在给模型看的工具 schema 里**，模型看不到、也无法用措辞（"旧版""当时"）
    越过 Plan 的 as_of。另外：**结构化 Plan 是"应用自己算出来的"，不是用户原文**——
    它在 system 消息里，与 `search_kb` 回来的原文片段是不同的信任级别，别把两者混为一谈。
11. **否定感知排行的两条已知缺口**（泛化 R3，见 `DEBUG_LOG.md` #48）：
    ① 不带关系词的**裸否定**判不出（"不要排名"、"不用帮我排名" 仍会被当成要排行）；
    ② `排行` / `排序` **本身不在** `RANK_WORDS` 里，所以"销量排行""按销量排序"
       不会被识别成排行意图（这是**既有**行为，本轮没有往词表里加词——避免在
       未验证的情况下引入新的误判）。两个缺口都只在"完全没提到其他指标"时才影响
       到答案形状，公开题库与泛化套件均为绿。

12. **R5 当前 live 状态**：本机当前没有配置 `LLM_API_KEY`，所以本轮 targeted
    DeepSeek multi-turn 与 official live 评测均为 **NOT RUN**。本文件第 8 节前面关于
    R4 历史 live 评测的记录仍是历史证据，不应解释成 R5 本机已重新接入真实 Key；R5
    当前可确认的是 public mock 100.00、preflight P1–P14 全过，以及 no-key mock 管线
    的结构化会话状态回归。

### 7.8 泛化 R5：Live 只接收结构化会话状态

R5 后 Service 在每轮开始按 `context_epoch` 读取 `ConversationState`，Planner 直接消费
它，不再把 SQLite transcript 传给 FollowUps 做自然语言重写。成功的 `data`、`doc`、
`hybrid` 回答才会生成 `ContextPatch` 并与 turn 在一次 SQLite transaction 中提交；
拒答与澄清只保留 transcript，语义 state 不变。

Live 的 system context 现在有两块可信输入：本轮 `Plan` 与结构化会话 state。上一轮
assistant 的完整答案不进入 prompt，因此旧轮次的数字不会被模型误当成当前事实；任何
当前轮经营数字仍必须从当前轮工具回执取得。没有 API Key 时继续使用 mock Answerer，
不会因为新增状态层改变启动方式。

上下文失效规则：`context_epoch = sha256(data_fingerprint + kb_index_key)`。数据或知识库
产物变化后，旧 session state 读取为空并在 trace 写 `session_state_invalidated`；trace
本身不随 session LRU 清理。SQLite 默认 `max_turns=12`、`max_sessions=500`，前者物理
删除旧 turn，后者按 access sequence 淘汰最久未访问 session。

R5 本机验证：`pytest tests/generalization/test_conversation_state.py -q` 为 **17 passed**，
`pytest tests/generalization` 为 **154 passed**；真实 live 评测因未配置
`LLM_API_KEY` 仍为 **NOT RUN**。
