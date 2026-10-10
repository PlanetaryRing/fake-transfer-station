# Fake Transfer Station

一个 CPA 风格的大模型整蛊中转站：配置本站 API Key 和模型目录，客户端发送请求后，服务端随机选取预设文本，按设定速度输出。没有真实模型调用。

参考 CLIProxyAPI 提交：`d318bcc3afb9ea8782862f5e8cdb541afdc9dfa5`。实现范围和差异见 [协议说明](docs/compatibility.md)。

## 启动

已有根目录 `.venv` 时，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m fake_transfer_station
```

默认优先读取 `config.yaml`，不存在则读取随项目提供的 `config.example.yaml`。

- 地址：`http://127.0.0.1:8317`
- 示例本站 Key：`sk-local-prank`
- 示例模型：`gpt-prank`、`claude-prank`、`gemini-prank`

使用自己的配置：将 `config.example.yaml` 复制为 `config.yaml` 并编辑，然后运行：

```powershell
.\.venv\Scripts\python.exe -m fake_transfer_station --config config.yaml
```

也可以设置 `PRANK_CONFIG` 环境变量。`--config` 优先于环境变量。支持 `--host`、`--port` 覆盖监听地址和端口。

仅检查配置：

```powershell
.\.venv\Scripts\python.exe -m fake_transfer_station --check-config
```

Linux / macOS 创建虚拟环境并安装 `requirements.txt` 后，用 `python -m fake_transfer_station` 启动。

## 修改内容与速度

编辑 `prank.content-pools`，每项使用 `text` 或 `file` 二选一：

```yaml
prank:
  default-content-pool: funny
  streaming:
    initial-delay-ms: 300
    chunk-size: 4
    keepalive-seconds: 15
  models:
    - id: "my-model"
      content-pool: funny
      streaming:
        tokens-per-second: 20
        tokens-per-second-offset: 4
    - id: "faster-model"
      content-pool: funny
      streaming:
        tokens-per-second: 50
        tokens-per-second-offset: 10
  content-pools:
    funny:
      selection: random
      items:
        - file: "./contents/dragon.txt"
          weight: 3
        - text: |-
            正在思考……
            今天适合摸鱼！
          weight: 1
```

以上是 `prank` 部分的示意，完整可运行配置见 `config.example.yaml`。

- 权重省略时为 `1`，必须大于零。每个请求独立抽取一次，允许连续重复。
- 每个模型必须在自己的 `streaming` 中填写速度。全局 `prank.streaming` 仅配置首字延迟、分块大小、心跳；这些辅助参数可以被模型单独覆盖。
- `chunk-size` 按 Unicode 码点计数，保留空白、换行及反斜杠；组合 emoji 可能跨块，拼接后不丢内容。
- 文件路径相对于配置文件所在目录，使用 UTF-8 编码。
- `prank.models[].streaming.tokens-per-second` 为该模型的基准速率，支持小数，`0` 表示不限速。
- `tokens-per-second-offset` 为绝对偏移量（同样单位 tokens/s，不是百分比），默认 `0`。例如基准 `30`、偏移 `5`，每个正文块独立均匀随机抽取 25～35 tokens/s，形成快慢波动；每次请求独立抽样。
- 偏移量必须非负且小于基准速度，避免零速/负速；不限速时偏移量必须为 `0`。模型别名沿用对应模型的速度。
- 估算方式与用量统计一致：完整文本的 UTF-8 字节数除以 4 向上取整。不是实际模型 tokenizer；同样字符数的中文、英文耗时可能不同。
- 按每块 token 增量除以本块抽到的速度，累加发送时刻；不逐块向上取整。偏移为 `0` 时，块大小不改变目标总耗时；开启波动后，块大小影响重新抽样的频率，整段时长也会随机变化。基准是抽样区间中心，并不保证整段平均速度恰好等于基准。网络背压可能使实际速度降低。
- `initial-delay-ms` 是开始正文计速前的额外等待，协议开始事件会先发送。首块还需等待该块对应的 token 时间。无波动时整体目标时长约为 `initial-delay-ms / 1000 + output_tokens / tokens-per-second`。
- 迁移个人配置：删除旧 `interval-ms` 和全局 `tokens-per-second`，为每个模型填写自己的速率与偏移量；全局速度字段现在会报配置错误，以免设置错位置。
- `keepalive-seconds` 控制 SSE 注释心跳，`0` 禁用。心跳不计入正文。
- 非流式请求立即返回完整文本，不逐块等待。
- 内容文件启动时加载；配置与内容修改后重启生效。
- 示例 `dragon.txt` 是示意小龙字符画，可以直接替换为你自己的奶龙字符画。

## API Key 与模型

`access.api-keys` 配置本站 Key，支持多个。CPA 风格的鉴权位置依次是：

1. `Authorization: Bearer <key>` 或直接填 Key。
2. `x-goog-api-key`。
3. `x-api-key`。
4. URL 参数 `key`。
5. URL 参数 `auth_token`。

任意一个候选匹配即通过。Key 列表为空时允许匿名访问。首页、健康检查和 OPTIONS 预检免鉴权。

`prank.models` 配置公开模型，`aliases` 可以配置可调用别名。别名不额外列入模型目录。各模型均可通过本项目实现的文本协议调用。

模型列表会根据 `client_version`、`Anthropic-Version` 和 `claude-cli` User-Agent 返回不同格式。Anthropic 模型目录默认沿用参考 CPA 的模型 ID 改写规则，可用 `upstream.claude.disable-cloaking-model-list: true` 关闭。

## 调用示例

```powershell
curl.exe http://127.0.0.1:8317/v1/models -H "Authorization: Bearer sk-local-prank"
```

Python 流式示例：

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8317/v1", api_key="sk-local-prank")
stream = client.chat.completions.create(
    model="gpt-prank",
    messages=[{"role": "user", "content": "你好"}],
    stream=True,
)
for chunk in stream:
    if chunk.choices:
        print(chunk.choices[0].delta.content or "", end="", flush=True)
```

Anthropic SDK 的 `base_url` 使用根地址 `http://127.0.0.1:8317`，模型可用 `claude-prank`。

客户端配置参考见 [docs/clients.md](docs/clients.md)。

## 查看请求记录

收到模型请求后，服务会在**启动工作目录**的 `logs/requests.jsonl` 追加一行记录，首次请求时自动创建目录及文件。建议始终在项目根目录启动。

```bash
cat logs/requests.jsonl
tail -f logs/requests.jsonl
```

每行是独立的 UTF-8 JSON，包含 UTC 时间、接口路径和完整请求体；中文直接显示，消息内部换行编码成 `\n`，避免一条记录占多行：

```json
{"time":"2026-10-10T12:30:00Z","path":"/v1/messages","body":{"model":"claude-prank","messages":[{"role":"user","content":"你好"}],"max_tokens":1024}}
```

HTTP 通过鉴权、完整读取且未超过请求大小上限后记录，发生模型/参数错误也会保留请求；无效 JSON 以原始文本保存。包含生成、token 计数和 compact 接口。WebSocket 每条 `response.create` / `response.append` 在生成前记录，保留客户端原始字段（含预热请求）。

首页、健康检查、模型列表、假管理登录和鉴权失败不记录。认证头、查询参数不写入文件；请求体内容完整保存。文件只追加，不自动轮转或删除。并发写入加锁，磁盘写入失败会在服务终端报错，但不会中止模型回复。Docker Compose 将 `/app/logs` 挂载到宿主机的 `./logs`；日志不提交 Git，也不进入镜像构建上下文。

## CPA 原版管理登录页

访问 `http://服务器地址:端口/management.html`，例如：

```text
http://127.0.0.1:8317/management.html
```

页面直接使用官方 Management Center **v1.26.0** 的原始发布文件，HTML/JS/CSS 均未修改。文件已随项目提供，启动服务无需下载或构建前端；现有 Dockerfile 也会将它复制到镜像。

本站 `/v8/management`、`/v0/management` 及其子路径统一返回 **403 `remote management is disabled`**，任何密钥都不能登录本站后台。错误显示使用原版前端逻辑，本站不会保存登录输入，也没有真实管理功能。OPTIONS 请求仍正常响应跨域预检。

原版的服务地址选择、浏览器存储和“记住密码”行为均保留。如果将地址改成另一台真实 CPA，连接的是那个服务，本站的拒绝逻辑不适用于它。

来源、版本和哈希见 [static/README.md](fake_transfer_station/static/README.md)，许可证保存在同目录的 `management.LICENSE`，也可通过 `/management.LICENSE` 查看。需要重新下载固定版本时执行：

```sh
python scripts/fetch_management.py
```

## Docker

```sh
docker compose up --build -d
```

默认挂载 `config.example.yaml`。使用个人配置时，将 `compose.yaml` 中配置挂载的 `source` 改为 `./config.yaml`。如果调整容器内端口，也需修改端口映射及健康检查。

反向代理参考 [deploy/nginx.conf](deploy/nginx.conf)，包含 SSE 关闭缓冲和 WebSocket 升级设置。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试文件包含配置校验、CPA 源码行为断言、SDK 聚合、真实 TCP 流式间隔、WebSocket 多轮、断连取消等。网络测试只启动本机 Uvicorn 测试服务，不访问外部模型服务。

**当前验证结果：97 项自动化测试通过。** 请求日志已通过 HTTP/WebSocket 原文、并发追加、中文、非模型接口排除、认证信息排除及写入失败测试。原版管理页面已通过完整文件哈希、公开访问、HEAD、许可证、真实 HTTP 和任意密钥拒绝测试；尚未做浏览器视觉验证。模型独立速率及偏移已通过快慢抽样、边界校验、别名映射、请求快照、中文/emoji、零偏移匀速及不限速测试；无波动调度已通过本地 TCP 流式测试。之前已对 `127.0.0.1:8317` 完成 OpenAI/Anthropic SDK、Gemini、Codex 路径别名及 WebSocket 验证；源码修改需重启服务生效。实际编程助手客户端、Docker 和反向代理尚未联调。

已启动服务也可以单独复测（读取配置中的本站 Key 和内容池，不输出 Key）：

```powershell
.\.venv\Scripts\python.exe -m tests.live_smoke --config config.example.yaml
```

自定义配置改用 `--config config.yaml`，非默认地址可加 `--base-url http://127.0.0.1:端口`。该命令向现有服务发请求，不另行启动服务。源码修复后需手动重启正在运行的服务。

## 行为说明

- 请求中的 prompts、tools、采样、推理和结构化输出参数不会改变预设文本。
- `max_tokens`、`max_output_tokens` 和 stop 参数不截断字符画；生成接口始终输出完整选中内容。
- Token 用量是 UTF-8 字节数除以 4 向上取整的模拟值，不是官方 tokenizer 结果。
- WebSocket 仅维护当前连接的上一轮 ID 和模型，HTTP Responses 不持久化会话。
- 服务启动入口关闭包含查询参数的访问日志，防止 `?key=` 被写入日志。

## 目录

```text
fake_transfer_station/   服务、配置、鉴权、协议和路由
contents/                UTF-8 预设内容
tests/                   测试代码
docs/                    客户端及兼容性说明
deploy/                  反向代理示例
config.example.yaml      完整示例配置
requirements.txt         已固定版本的依赖
```
