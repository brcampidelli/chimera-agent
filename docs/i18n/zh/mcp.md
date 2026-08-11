---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
---

# 连接 MCP 服务器

MCP（Model Context Protocol）是把外部工具接入 agent 的标准方式——GitHub、文件系统、Notion、
数据库，以及数百个其他服务器都支持这个协议。Chimera 内置了一流的 MCP 客户端：任何服务器的工具
都会变成普通的 Chimera 工具，和内置工具位于同一个注册表中，受同样的白名单/内核/台账层管控。

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
