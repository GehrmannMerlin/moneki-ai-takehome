# LLM 接入说明

> 模板依据：`docs/API_CONTRACT.md` §7.4（八节）。
> **本文档的状态：P0 骨架。** 第 1–6 与第 8 节记录的是 starter 现状与我的接入计划；
> 第 7 节在 P3 跑完 `eval/llm_gateway.py preflight` 后回填真实输出。
> 每一节最后都标注了它会在哪个阶段被"写完"。

---

## 1. 用了什么

| 项 | 值 | 状态 |
|---|---|---|
| 厂商 | DeepSeek（`https://api.deepseek.com`） | 计划 |
| 模型名 | `deepseek-flash` | 计划 |
| 协议 | OpenAI 兼容 Chat Completions（`POST {LLM_BASE_URL}/chat/completions`） | 计划 |
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

脱敏样例（Key 从不入 trace，`Authorization` 头不记录）：

```json
{"endpoint": "http://127.0.0.1:8900/chat/completions", "model": "deepseek-flash",
 "messages": 3, "tools": 11, "prompt": "[{\"role\": \"system\", \"content\": \"…\"}]",
 "status": 200, "finish_reason": "stop", "content_chars": 148,
 "has_reasoning": true, "took_ms": 3120.4}
```

> **已知不足（P3 修）**：`llm.py:184-186` 的 `_preview()` 把 prompt 与 raw_content
> **截断到 4000 字**，而契约 §6 要求"完整提示词和模型原始输出"。P3 会改成完整留存
> （体积大时写文件、trace 里存路径）。这一条现在写在《8. 已知限制》里，不藏。

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

### 7.1 `preflight` 输出

_待 P3 回填。_ 计划：

```bash
python eval/llm_gateway.py preflight --service-url http://localhost:8000
# 按它打印的提示用它给出的 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 重启服务，再回车
```

会在 P3 同时做两件事：
1. 用 `eval/llm_gateway.py fake` 起假模型服务，把 preflight 的 16 个场景
   （normal / thinking_starved / empty_content / json_empty / bad_tool_args /
   content_filter / insufficient_resource / aborted / http_401/402/422/429/500/503 /
   slow / hang）逐条写成红测试，每条断言"HTTP 200 + 合法 JSON + 思考内容不进 answer +
   真实原因入 trace"；
2. 用真实 DeepSeek Key 跑一遍 `proxy`，把请求与响应存档作为可观察性证据。

### 7.2 异常三类怎么验证（契约 §7.4 第 7 节）

| 异常 | 怎么造 | 期望 |
|---|---|---|
| 空回答 | `llm_gateway.py fake` 的 `empty_content` / `json_empty` 场景 | `/api/chat` 200 + `answer_type=refusal`，trace 记 `empty_content` |
| 超时 | `fake` 的 `hang` 场景（连接建立但永不响应） | read timeout 独立于 connect timeout，到点返回结构化 refusal，不挂起 |
| 报错 | `fake` 的 `http_401/402/422/429/500/503` 场景 | 每种都 200 + refusal，trace 记真实状态码；429/500/503 先重试一次 |

---

## 8. 已知限制

清楚但还没解决的，一并写在这里。

1. **trace 里的提示词被截断到 4000 字**（`llm.py:184-186` 的 `_preview`）。
   契约 §6 要求完整提示词与模型原始输出。**P3 修**：完整留存，体积大就写文件、trace 存路径。
2. **流式输出还没做**（契约 §7.3 最后一行）。当前 `/api/chat` 是非流式；
   `delta.reasoning_content` → `delta.content` 的顺序处理、前端"思考中"状态都在 P4。
   预检里与流式有关的项会显示"未检查"而不是"通过"。
3. **`CHAT_BUDGET` 默认 150 秒**，不是契约的 180 秒上限。留了 30 秒余量；
   如果 P3 实测发现思考 + 多轮工具调用吃紧，会调到贴近 180 秒并在这里更新。
4. **思考模式开关未显式设置**。契约 §7.3 允许自己决定并说明理由；
   `deepseek-flash` 默认开思考。**当前计划：保持开启**（规划更稳、多轮工具调用更可靠），
   理由会写进 README；对应的代价是更慢、更贵，以及必须把 assistant 消息连同
   `reasoning_content` 整条回传（`llm.py:43` 的 `LLMReply.message` 已经是这个设计）。
5. **`tools` 声明里的工具集会在 P3 从 5 个扩到 11 个**，届时本节与第 1 节的工具描述同步更新。
6. **`temperature` / `presence_penalty` / `frequency_penalty` 不发**——思考模式下不生效，
   发了只会让"兼容参数"这条预检项变红。数字一律由代码从工具结果渲染，不靠模型确定性。
