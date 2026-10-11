# CPA 兼容范围与来源

## 基线

- 项目：<https://github.com/router-for-me/CLIProxyAPI>
- 参考提交：`d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5`
- 配置风格：v8，普通独立运行模式。
- 这是独立 Python 实现的文本响应模拟服务；兼容项以本文及测试文件为界，不是完整 CPA 分支移植。

参考源文件：

- [路由与模型分流](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/internal/api/server_routes.go)
- [Key 解析](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/internal/access/config_access/provider.go)
- [鉴权及 CORS](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/internal/api/server_middleware.go)
- [Anthropic 模型 ID 转换](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/internal/client/claude/models/models.go)
- [Codex 模型目录构造](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/internal/client/codex/models/models.go)
- [Chat Completions](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/sdk/api/handlers/openai/openai_handlers.go)
- [Responses](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/sdk/api/handlers/openai/openai_responses_handlers.go)
- [Responses WebSocket](https://github.com/router-for-me/CLIProxyAPI/blob/d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5/sdk/api/handlers/openai/openai_responses_websocket.go)

## 已编写接口

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/` | CPA 风格服务信息 |
| GET / HEAD | `/management.html` | 官方 v1.26.0 原版静态页面，字节未修改 |
| GET | `/management.LICENSE` | 原版页面的 MIT 许可证 |
| GET / HEAD / POST / PUT / PATCH / DELETE | `/v8/management`、`/v0/management` 及其子路径 | 固定 403，管理未启用；OPTIONS 由全局 CORS 处理 |
| GET / HEAD | `/healthz` | 健康检查 |
| GET | `/v1/models` | OpenAI、Anthropic 或 Codex 模型目录 |
| GET | `/v1/models/{model}` | 对应目录格式的模型详情，支持路径含斜杠 |
| POST | `/v1/chat/completions` | assistant 文本，JSON / SSE |
| POST | `/v1/completions` | text completion，JSON / SSE |
| POST | `/v1/messages` | Anthropic message，JSON / SSE |
| POST | `/v1/messages/count_tokens` | 模拟输入计数 |
| POST | `/v1/responses` | Responses，JSON / SSE |
| WebSocket | `/v1/responses` | JSON 事件流 |
| POST | `/v1/responses/compact` | 模拟 compaction 对象；stream=true 返回 400 |
| POST / WebSocket | `/backend-api/codex/responses` | 对应 Responses 别名 |
| POST | `/backend-api/codex/responses/compact` | compact 别名 |
| GET | `/v1beta/models` | Gemini 模型目录 |
| GET | `/v1beta/models/{model}` | Gemini 模型详情 |
| POST | `/v1beta/models/{model}:generateContent` | Gemini 文本 JSON |
| POST | `/v1beta/models/{model}:streamGenerateContent` | Gemini SSE，推荐 `?alt=sse` |
| POST | `/v1beta/models/{model}:countTokens` | 模拟输入计数 |

### 模型列表规则

1. 参数存在 `client_version`，即使值为空，也选择 Codex 目录。
2. 否则存在非空 `Anthropic-Version` 或 User-Agent 以 `claude-cli` 开头，选择 Anthropic 目录。
3. 其余为 OpenAI `object: list`。

Anthropic 目录按展示名及 ID 排序，包含 `has_more/first_id/last_id`。非 `claude-` 开头的 ID 默认转换成 `claude-fable-5-dd-` 加原 ID 的反转字符串，请求时还原；支持 `model(high)` 形式的思考后缀。

Codex 返回 `models`，含 slug、上下文长度、指令模板及必要的 nullable 字段。使用精简的本地元数据模板，未复制 CPA 的官方模型目录或随版本变化的完整能力映射。需要对具体 Codex 版本实测。

### 错误与流式

- 缺少 Key：401 `{"error":"Missing API key"}`。
- 无效 Key：401 `{"error":"Invalid API key"}`。
- 未知生成模型：502，`server_error`。
- 无效 JSON 或基础请求结构：400。
- 鉴权、JSON、模型解析均在开始 SSE 前处理。
- Chat / Completions 发送终止原因和 `[DONE]`；`include_usage` 可请求末尾统计块。
- Messages 使用消息及内容块生命周期；Responses 使用序号递增的生命周期事件。
- 断开连接时取消待发送文本与睡眠任务。

## 模拟行为与已知边界

- 所有模型都生成预设正文。工具定义和多模态输入块作为不透明数据接收，输出为普通文本，不生成工具调用或多模态结果。
- 预设思考支持内容项绑定的加权候选，默认关闭，沿用模型速率及偏移。Chat 使用 `reasoning_content` 扩展；Responses/WebSocket 使用 reasoning summary 事件；Anthropic 使用 thinking 块（本站模拟签名，不是官方签名）；Gemini 使用 thought 部分。传统 Completions、compact 和 WebSocket 预热无思考输出。开关优先级和示例详见 README“自定义思考文本”。
- 支持未知请求扩展字段；必要包络字段仍做类型校验。`n` 仅支持 1。
- `compact` 保留用户消息并追加 mock compaction 标记，标记不是真实加密上下文。后续请求可原样带回；服务器不解密或恢复历史。
- WebSocket 支持 `response.create`、`response.append`、`generate:false` 预热、同连接上一轮 ID 续接。未知上一轮 ID 返回 409。并发生成请求返回 409。
- `response.interrupt` 取消当前生成并返回本项目的 `response_interrupted` 错误；不是完整 CPA 的上游双工 steering 实现。
- HTTP `previous_response_id` 作为兼容字段接收，不维护服务端会话；完整预设输出不依赖历史。
- Gemini 流固定为 SSE，没有实现不带 `alt=sse` 时的 JSON 数组流。
- 基础配置支持 `server`、`access`、`upstream.claude.disable-cloaking-model-list`，以及旧版顶层 `host/port/api-keys`。同字段 v8 值优先。不是任意完整 CPA 配置文件的替代解析器，未知配置字段报错。
- 模型目录不提供分页，返回全部配置模型。
- CPA 的图像、音频、视频、Realtime、Interactions、Alpha Search、Home、OAuth 尚未实现；未注册路径返回 404。
- 管理页面直接分发官方 Management Center v1.26.0 发布文件；所有本站管理 API 固定拒绝，未实现真实管理操作。前端保留原版的浏览器存储和服务器地址选择行为，不保证填写另一台真实 CPA 的地址时也无法登录。来源和 SHA-256 见 `fake_transfer_station/static/README.md`。
- CORS 沿用 CPA 的通配方式，暴露本服务实际使用的请求 ID 头；未复刻 CPA 全部版本与内部追踪头。
- 模型接口请求体追加到启动工作目录的 `logs/requests.jsonl`，包含 UTC 时间、路径和完整请求体；HTTP 在完整读取后、参数/模型验证前记录，WebSocket 记录 create/append 原始输入。无会话归组、无响应回放，日志不包含请求头及查询参数。鉴权失败、超出读取上限及非模型接口不记录。日志只追加，Docker Compose 持久化到宿主机 `./logs`。

## 验证状态

源码行为已用于编写鉴权和目录断言，响应类型参考已安装 SDK。

- `python -m fake_transfer_station --check-config`：通过，3 个模型、1 个内容池。
- `python -m pytest -q`：**135 passed**，包含真实本地 TCP 服务、SDK 聚合、WebSocket、tokens/s 调度及断连取消。调速修改新增模型独立速率、偏移边界、快慢抽样、零偏移、别名映射和请求快照测试；确定性时钟测试验证波动速度及累计 token 计费一致性。
- 预设思考：默认关闭与显式禁用、绑定加权选择、文件加载、各协议输出、Responses 事件严格 Schema、OpenAI/Anthropic SDK 最终聚合、WebSocket、共享速度/偏移、仅一次首字延迟和取消测试通过。完整思考配置示例检查通过。
- 请求日志：HTTP 各协议原文、WebSocket 多轮、中文/无效 JSON、并发追加、排除非模型接口与认证头、写入失败不影响回复等测试通过。
- 管理页面：官方发布 SHA-256 校验通过，HTTP 返回的文件字节与官方资产一致；公开访问、HEAD、许可证、v8/v0 多方法及多种密钥固定拒绝测试通过。未启动浏览器验证视觉及点击交互。
- `python -m tests.live_smoke --config config.example.yaml`：已针对用户启动的 `127.0.0.1:8317` 完成服务健康、鉴权、模型目录、错误响应、Chat/Completions、Responses、Messages、Gemini、compact、Codex 别名及 WebSocket 预热续接检查。
- 重启后再次完成全部现有服务 smoke 检查。此次 Chat 流收到 51 个正文块，首块到末块约 4.68 秒，正文与配置内容池中的某一项完全一致；随机选择不同内容时，块数和总时长会变化。
- 测试发现 Responses 用量对象缺少当前 OpenAI SDK 要求的 `input_tokens_details.cache_write_tokens`，已补为 `0`。用户重启后已确认修复生效，并对实际 HTTP 响应执行 `Response.model_validate` 严格 Schema 校验，通过。

尚未进行真实 CPA 实例的差分请求测试，也未启动 Codex、Claude Code 或 OpenCode 联调。Docker 构建及反向代理尚未验证。

最新调速配置仅位于 `prank.models[].streaming`：每个模型必填 `tokens-per-second`，可设 `tokens-per-second-offset`（默认 0）。每块独立均匀抽取基准 ± 偏移的速度，以 token 增量计算耗时后累加发送时刻；偏移必须小于正基准。`0` 基准表示不限速，此时偏移也必须为 0。全局只保留首字延迟、分块大小和心跳设置。

沿用 UTF-8 字节数 / 4 的估算口径，完整输出向上取整，累计调度避免逐块取整导致速度失真。启用波动后整段总耗时随抽样而变化；无波动时分块大小不影响目标总耗时。上述用户服务时长记录来自此前版本，用户常驻进程需重启加载新代码。
