---
source_sha256: a4a013be96904287074cc9859aaa710195511afc3c9b81627392342b7ef34e27
---

# 连接 MCP 服务器

MCP（Model Context Protocol）是把外部工具接入 agent 的标准方式——GitHub、文件系统、Notion、
数据库，以及数百个其他服务器都支持这个协议。Chimera 内置了一流的 MCP 客户端：任何服务器的工具
都会变成普通的 Chimera 工具，和内置工具位于同一个注册表中，受同样的白名单/内核/台账层管控。

## 远程 streamable HTTP

对于使用 streamable HTTP 的 MCP 端点，请配置 `url` 而不是 `command`。CLI 接受
`chimera mcp add NAME --url https://host.example/mcp`；用 `--token-env ENVIRONMENT_VARIABLE`
进行认证，即可在运行时解析 bearer token，而不把它的值保存到 `mcp.json` 中。

对于 OAuth authorization-code + PKCE，请在 `mcp.json` 中配置 `oauth_authorization_url`、
`oauth_token_url` 和 `oauth_client_id`（或使用 `chimera mcp add` 的对应选项）。Chimera 会打开
授权页面，在 loopback 上接收回调，用它的 PKCE verifier 交换授权码，并把得到的 token 存入操作系统
的凭据保险库。存储的 token 只以 `Authorization: Bearer` 头发送，绝不会写入日志。请安装可选的
`secrets` extra 以支持操作系统保险库；如果没有可用的保险库，OAuth 设置会失败关闭（fail closed）。

登录只会由一次显式的 Test 触发（`chimera mcp test NAME` 或界面上的 Test 按钮）。启动时，连接池和
autoload 从不打开浏览器：没有已存储 token 的服务器会被跳过，日志会提示运行 Test。凭据（bearer
token 或 OAuth 交换）只会通过 https 发送，或通过普通 http 发往 loopback；带 token 的远程
`http://` URL 会被拒绝。

远程服务器和 stdio 服务器经过同样配置的 MCP 工具接口、同样的注册表命名空间、同样的长连接池、探测
命令、错误处理和观察围栏。请把远程工具的元数据和结果视为不可信的服务器内容。

## 安装客户端 extra

MCP 客户端放在一个可选 extra 里，好让核心包保持轻量：

```bash
uv sync --extra mcp
```

大多数服务器是 Node 包，所以你还需要 `npx`（随 Node.js 一起提供）。

## 60 秒冒烟测试（无需凭据）

参考实现的文件系统服务器不需要任何 token——它只是在你指定的目录上暴露读写工具：

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

把这个 registry 交给一个 `Agent`（或参见 `examples/mcp_github.py` 的完整循环），此后模型就可以
像调用其他任何工具一样调用该服务器的工具了。

## 一个真实的服务器：GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

这就是整个集成过程：约 26 个 GitHub 工具（搜索仓库、读取文件、列出 issue、创建 PR……）就会
出现在注册表里。可端到端运行的完整版本见：
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py)。

## 它如何融入安全层

MCP 工具就是普通的 `Tool` 对象，因此一切都能自然组合起来：

- **按会话的白名单** —— `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  只授予本次运行需要的 MCP 工具；未被授予的工具永远不会传给模型。
- **治理内核** —— `govern_registry(...)` 会像管控任何 shell 命令一样，对 MCP 调用做
  允许/警告/复核/阻止的把关。
- **污点台账** —— 用 `ledger_registry(...)` 包装后，MCP 的抓取行为也会被记录；不过目前只有
  在 `FETCH_TOOLS` 中列出的工具会被自动分类，因此请把 MCP 返回的内容一律当作不可信内容对待，
  当服务器会拉取外部数据时，优先在 `--taint --guard` 语义下运行。
- **服务器的 `instructions`** — 服务器在 `initialize` 时返回的文本会被有意丢弃：它是不可信的服务器文本，而且没有机制像围栏抓取那样把它标记为数据。代价是服务器的使用说明永远到不了模型；而转发它的宿主也不应依赖它（arXiv 2608.08467：在有搜索工具时，24 个模型中有 9 个在放入服务器指令的查询上低于 15%）。在 taint 下把它作为围栏数据转发，仍是待办，尚未完成。丢弃它**并不是**针对服务器所写文本的边界：同一服务器的工具名称和描述会按服务器写的原样、不加围栏地到达模型；你连接的服务器，就是模型会读到其文字的服务器。

## Chimera *作为* MCP 服务器

上面讲的是 Chimera 调用其他工具的一面。反过来也同样可行：把 Chimera **作为**一个 MCP 服务器
运行，这样任何 MCP 客户端——Claude Desktop、某个 IDE、另一个 agent——都可以把整个引擎当作三个
工具来调用。

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

它会暴露：

| 工具 | 作用 |
| --- | --- |
| `chimera_solve` | 通过规划 + 验证或回滚，自主完成一项任务；返回结果。 |
| `chimera_fuse` | 通过 LLM-Fusion 引擎（面板 → 评审者 → 综合器）回答一个提示词。 |
| `chimera_memory_search` | 检索 Chimera 的长期记忆，返回最相关的事实。 |

把某个 MCP 客户端指向它，将其作为一个 stdio 服务器。以 Claude Desktop 为例，在其配置中加入：

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` 需要一个 provider 密钥才能使用 `chimera_solve`/`chimera_fuse`（记忆检索则无需密钥即可
使用）。加上 `--fuse` 可以让求解器的深度推理轮次改走融合路径，加上 `--no-memory` 则会跳过记忆
召回。由于通信走的是 stdio，所有日志都会输出到 stderr——stdout 只携带协议数据。

## 让 Claude 操作桌面应用

`chimera serve --mcp` 会构建自己的智能体。`chimera mcp desktop` 什么也不构建：它是你**已经打开的**
桌面应用的遥控器，所以 Claude 看到的对话、运行和审批与你看到的相同，它发起的一切都在应用的治理下、
在应用的界面上运行。

1. 在应用中打开 **设置 → Claude**，开启 **允许 Claude 操作此应用**。
2. 在 Claude Code 中注册该服务器（或把同样的命令加入 Claude Desktop 的配置）：

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

开启第一个开关后，Claude 可以读取和发起对话（`desktop_send`）、运行、批处理、看板和 cron 任务，
搜索和编辑记忆，读取文件和 git 状态。它发起的运行带着你配置的姿态；试图放宽姿态的请求——`verify`
命令、在主机上执行、其他智能体、自动审批——都会被拒绝。审批始终由你决定：`desktop_approvals`
只列出它们。当某一轮停在审批上时，`desktop_send` 会立即返回，说明正在等你，而这一轮会在应用里继续；
`desktop_job` 会报告它如何结束。

第二个开关 **完全控制** 会增加 `desktop_approve`（回答审批和带关卡的步骤）和 `desktop_settings`
（编辑设置和智能体的身份）。开启后，Claude 可以在你不在场时批准操作——智能体读到的带有提示注入的
网页或消息也可能诱使它这样做。关闭时，这两个工具根本不会出现在列表中；即便被调用，应用也会拒绝。

无论哪个开关开着，有些决定仍然归你。由哪个模型回答（每个模型设置、后备链、融合的评审组、裁判和
合成者、成本模式、级联和已验证回答）以及应用是否运行计划任务，Claude 只能*建议*：不会写入任何内容，
应用会显示一张卡片，列出每个设置的当前值和建议值，由你在那里批准或拒绝。Claude 无法通过任何途径
回答这张卡片；如果在你批准之前设置已经改变，则不会应用任何内容。此外，给文件夹授予命令权限、在
Runner 中运行命令、启动消息机器人以及保存带工具权限的智能体，都会被桥接完全拒绝：这些由你在应用中
完成。

此外，无论哪个开关开着，Claude 启动的运行都使用你配置的模型，且不会超出你配置的姿态。指定模型、
角色计划、配置档、融合评审组或其他智能体的请求会被拒绝；更宽的姿态同样会被拒绝——更大的范围、更宽松
的审批、在你未授予 shell 的地方进行主机执行或 `verify` 命令，或自动批准。要求一次运行做得更少（只读，
或总是审批）是允许的。

任何开关都不允许的事：读取或写入 API 密钥、令牌或 webhook。设置编辑会拒绝凭据名称，携带密钥或
分享链接的路由不可达，凭据文件（`.env`、私钥）无法被读取、写入或搜索，所有结果都会清除凭据值。
也不能把工作区指向应用的数据文件夹，或包含它的文件夹（例如你的主目录）：审批的答复就保存在那里，
写到那里的文件就等于回答了一项审批。

连接方式：开关开启期间，应用会写入 `~/.chimera/desktop-bridge.json`（其回环 API 的 URL 和一个随机
令牌；在 POSIX 上仅你可读，在 Windows 上位于你的用户配置文件内）。关闭开关或关闭应用会删除该文件并
作废令牌。应用关闭时，每个工具都会回答 "Chimera desktop is not running, or 'Allow Claude to
operate this app' is off in Settings."。Claude 在连接时获取工具列表；开启或关闭 **完全控制** 后，
请重新连接服务器（在 Claude Code 中使用 `/mcp`）以看到新列表。

## 使用 A2A（agent 对 agent）

MCP 把 agent 连接到*工具*；**A2A**（Agent2Agent，Linux Foundation）把 agent 彼此*相互*连接
起来——它是 LangGraph、CrewAI、AutoGen 的原生能力。Chimera 也支持它，因此一个
LangGraph/CrewAI 编排器可以把任务委派给 Chimera，并取回一个已完成的结果。

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` 会给 HTTP 服务器增加两个路由：

| 路由 | 用途 |
| --- | --- |
| `GET /.well-known/agent.json` | Agent Card —— 身份信息 + 对外宣称的能力（solve、fuse）。 |
| `POST /a2a` | JSON-RPC 2.0 任务生命周期接口：`message/send`、`message/stream`、`tasks/get`、`tasks/cancel`。 |

客户端发送带文本部分的 `message/send`；Chimera 运行自治 agent，并把结果作为一条 agent 消息，
以 `completed`（或 `failed`）状态的任务形式返回。也可以发送 `message/stream`，得到一个
**Server-Sent Events** 流：先是处于 `working` 状态的任务，运行结束后再是 `completed`/`failed`
状态的任务——这样编排器无需轮询就能看到进度。该 agent card 会对外宣称
`capabilities.streaming: true`。

**范围说明，诚实地讲：** 目前这个流只发出两个事件（working → 最终结果），并不是逐步的 token
增量；推送通知（push notification）也尚未实现。这是一个符合规范、无需轮询的流——足以作为
LangGraph/CrewAI 应用中一个一流的可流式节点。

## 故障排查

- `TimeoutError: MCP server ... did not become ready` —— 说明命令没能启动。在终端里手动运行同
  样的 `npx ...` 命令行，看看具体报什么错（缺少 token、缺少 Node、首次运行的包下载太慢——可以
  调大 `connect_timeout`）。
- `ModuleNotFoundError: mcp` —— 安装对应的 extra：`uv sync --extra mcp`。
- 工具名冲突 —— 始终传入一个 `name_prefix`。
- 该会话会在你脚本的整个生命周期内，把服务器当作一个子进程来运行；调用 `connector` 的会话
  `close()`（或者干脆让进程退出）来关闭它。
