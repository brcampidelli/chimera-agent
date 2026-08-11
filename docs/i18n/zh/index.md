---
source_sha256: fe1348e242b1851c75bb1525ecf723afe068c499ed372335aca5e281cc72ba04
---

# Chimera

一个开源（Apache-2.0）、能自我演进的 AI agent，其推理核心在一个成本感知路由器背后**融合多个
模型**（面板 → 评审者 → 综合器）——并配有治理内核、沙箱，以及一份会不断学习的记忆。

本站以任务为导向：挑一个你想做的事开始。

<div class="grid cards" markdown>

- **:material-rocket-launch: 快速上手**
  安装、添加一个密钥，五分钟内运行你的第一个任务。
  [安装与首次运行 →](usage.md)

- **:material-toolbox: 做点真事**
  可直接运行的配方：邮件分诊、每日研究简报、仓库看门狗。
  [配方 →](recipes.md)

- **:material-power-plug: 连接工具**
  接入任意 MCP 服务器（GitHub、文件系统……）。
  [MCP 服务器 →](mcp.md)

- **:material-server: 让它运转起来**
  在一台小型服务器上 7×24 运行；安排定时任务；把结果送达到聊天工具。
  [部署 →](deploy.md)

- **:material-shield-lock: 安全**
  治理、沙箱、污点追踪——以及它们诚实的边界。
  [安全 →](security.md)

- **:material-sitemap: 理解它**
  融合核心、演进机制与安全层是如何拼在一起的。
  [架构 →](architecture.md)

</div>

## 一行命令

```bash
uv sync --extra dev && uv run chimera init
```

然后试试 `chimera run "..."`，或者来一份真正的配方：

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## 默认诚实

Chimera 目前处于 **alpha** 阶段。它内置了纵深防御，但文档会明确说明每一道防线在哪里止步——
连注入防御都会公开一个实测的数字（`chimera redteam`）。参见[安全](security.md)。
