# 客户端接入

以下是本服务端点与客户端的配置参考。当前尚未启动这些客户端进行联调，不同版本的配置字段可能存在差异。

## 通用 OpenAI 客户端

- Base URL：`http://127.0.0.1:8317/v1`
- Key：`sk-local-prank`（或你的配置值）
- 模型：`gpt-prank`
- 协议：Chat Completions 或 Responses

模型列表使用 `/v1/models`。部分客户端会自行补 `/v1`，此时在其界面填写根地址。

## Claude Code / Anthropic SDK

PowerShell 环境变量示例：

```powershell
$env:ANTHROPIC_BASE_URL = "http://127.0.0.1:8317"
$env:ANTHROPIC_AUTH_TOKEN = "sk-local-prank"
$env:ANTHROPIC_MODEL = "claude-prank"
```

如客户端使用 API Key 模式，将本站 Key 配置为 `ANTHROPIC_API_KEY`。本服务接受 Bearer 及 `x-api-key` 两种方式。避免同时保留指向其他服务的认证环境变量。

Anthropic SDK 示例：

```python
from anthropic import Anthropic

client = Anthropic(base_url="http://127.0.0.1:8317", api_key="sk-local-prank")
with client.messages.stream(
    model="claude-prank", max_tokens=4096,
    messages=[{"role": "user", "content": "你好"}],
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

## Codex

自定义提供方配置示意（合并到你自己的客户端配置中）：

```toml
model = "gpt-prank"
model_provider = "local_prank"

[model_providers.local_prank]
name = "Local CPA"
base_url = "http://127.0.0.1:8317/v1"
env_key = "PRANK_API_KEY"
wire_api = "responses"
```

设置 `PRANK_API_KEY=sk-local-prank`。服务同时提供 Responses HTTP/SSE、WebSocket 和 `/backend-api/codex` 别名。

Codex 专用模型目录地址：`http://127.0.0.1:8317/v1/models?client_version=cpa`，查询需携带本站 Key。若客户端支持自定义目录 URL，应按该版本文档配置。

## OpenCode

在客户端的自定义 OpenAI-compatible 提供方中设置上述 Base URL、Key 和模型 ID。Chat Completions 与 Responses 入口都已编写。

本项目没有修改你的 OpenCode 配置；客户端侧配置请使用你当前版本支持的自定义提供方字段。

## Gemini

- 根地址：`http://127.0.0.1:8317`
- 鉴权：`x-goog-api-key: sk-local-prank`
- 模型：`gemini-prank`
- 流式路径：`/v1beta/models/gemini-prank:streamGenerateContent?alt=sse`

请求体示例：

```json
{"contents":[{"role":"user","parts":[{"text":"你好"}]}]}
```
