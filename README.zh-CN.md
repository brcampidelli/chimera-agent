<div align="center">

<img src="assets/logo-wide.png" alt="Chimera 标志" width="460" />

# Chimera

**一个开源、自我进化的 AI 智能体，其推理核心是 LLM 融合（Fusion）引擎。**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-加入-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <b>中文</b> · <a href="README.ja.md">日本語</a></sub>

</div>

Chimera **每次请求都融合多个 LLM** —— 一条受 OpenRouter Fusion 启发的 **专家组 → 评审 → 综合器** 流水线 ——
而不是依赖单一前沿模型，并且**随时间自我改进**（记忆 → 技能 → 模型），同时抵抗限制当今智能体的*持续进化退化*。

> **状态：** 早期开发（0.1.x）。完整的构建计划（M0–M7）已实现 —— 四个能力层级 + 融合引擎 + 自我进化
> + 治理内核 —— 另加**接口层**（chat、TUI、HTTP 网关）、**可选的模型进化**，以及**功能层**
> （Vision、交付模式、Pets 等）。
> 224 个测试（外加可选的在线集成）· `mypy --strict` 通过 · `ruff` 通过。

---

## 为什么选择 Chimera

现有框架各自只在一个维度上强：Hermes/OpenClaw 能进化技能但使用单一模型；CrewAI/LangGraph 编排得好但不会学习；
TrustClaw/NemoClaw/ZeroClaw 带来安全/沙箱但不会进化。**Chimera 把这四者结合起来：**

- 🧬 **融合即推理** —— 专家组→评审→综合器引擎是推理核心，而非附加件。提升来自*综合*步骤本身，而不仅是模型多样性。
- 🪜 **一条进阶路线上的四个能力层级** —— 增强工具 → 单任务自主 → 多智能体团队 → 自我进化生态。
- ♻️ **多层自我进化**，明确对抗持续进化退化（外部化状态、抗漂移上下文、verify-or-revert、经验缓冲区）。
- 🛡️ **同样会自我改进的治理内核** —— allow/warn/block/review，并带有经过静态校验的自我修改面。

## 功能

**推理与自主**
- **LLM-Fusion 引擎** —— 与厂商无关的前沿 + 开源模型专家组、一个揭示共识/矛盾/盲点的评审，以及一个综合器；一个**成本感知路由器**只在划算时才融合（工具调用回合保持单模型）。
- **第 2 层自主** —— 规划 → 执行 → Manager 评审 → **verify-or-revert**（工作区快照/恢复 + 命令校验器），并带有类 git 的经验缓冲区。
- **多智能体团队** —— 角色专精、顺序与监督型 crew、MOC 消息整合、共享记忆、并行评审。

**自我进化与治理**
- **自我进化** —— 一个 Memory Manager（ADD/UPDATE/DELETE/NOOP 去重）、一个*自己编写并测试技能*的技能进化器（提出 → 测试 → 保留/丢弃）、自学习 cron，以及一个衡量退化的**持续进化基准**（外加 EvoClaw 朴素 vs 受护 压力测试）。
- **可选的模型进化** —— `solve` 收集轨迹；`evolve` 将其整理为 SFT/DPO 数据集并生成可运行的 LoRA 配方。训练保持**外部且可选** —— 绝不自动进行。
- **治理与安全** —— 一个自我改进的信任内核（allow/warn/block/review）、一个用于自我修改编辑面的静态校验器、一个仅追加的审计日志，以及受治理的工具。

**模型提供方**
- **任意模型，一个接口** —— 通过 LiteLLM 与厂商无关（用 `provider/model` 标识访问 100+ 模型）；为 OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek 提供一等密钥。
- **自托管且具弹性** —— 为 **Ollama/vLLM** 提供自定义端点（`CHIMERA_API_BASE`）、跨模型的**回退链**、带轮询密钥轮换的**凭据池**，以及在 `chat`/`tui` 中实时切换 **`/model`**。

**接口与集成**
- **CLI 优先，外加多种接口** —— 一个 `chat` REPL、一个全屏 **TUI**（Textual），以及一个每个会话独立对话（与记忆）的 **消息网关** HTTP 服务器。
- **集成** —— 一等的 **MCP** 客户端（stdio）+ 一个 **OpenAPI/REST → 工具** 导入器，让你接入任意平台或 API。
- **Cron 与主动性** —— 人工指派与自学习的定时任务。
- **迁移** —— 从 Hermes Agent / OpenClaw 导入配置、技能和**长期记忆**（记忆是*合并*，绝不覆盖）。

**内置附加项**
- **Vision**（粘贴图片）、**交付模式**（生成精炼、自洽的成果物）和一个 **Pet** 伙伴 —— 另加用于网页搜索、图像生成、TTS/语音等的预置凭据槽（`chimera features` 显示哪些已就绪以及各自所需）。

## 快速开始

需要 Python **3.11+**（推荐 3.12+）和 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync --extra dev
cp .env.example .env        # 至少设置一个模型提供方密钥（推荐 OpenRouter）
uv run chimera doctor       # 检查你的环境
```

## 命令

```bash
chimera doctor / models / features    # 状态、配置、可选能力
chimera chat                          # 交互式多轮助手（你的得力助手）
chimera tui                           # 全屏终端应用（Textual）
chimera serve                         # 消息网关 HTTP 服务器（每会话独立）
chimera run "PROMPT" --image pic.png   # 第 1 层单次调用（用 --image 支持视觉）
chimera deliver "一个发布计划" -o plan.md   # 交付模式：生成精炼成果物
chimera fuse "PROMPT" --show-panel     # LLM-Fusion：专家组 -> 评审 -> 综合器
chimera agent "任务" --fuse --guard    # ReAct 工具循环（受治理的工具调用）
chimera solve "任务" --verify "pytest -q"   # 第 2 层自主：规划 -> verify-or-revert
chimera crew "任务" --mode supervisor  # 第 3 层多智能体 crew
chimera meta "一个用于 X 的智能体"          # 第 4 层元智能体：设计一个专用智能体
chimera memory add "一个持久事实"    # 经整理的长期记忆（去重）
chimera cron add 名称 "0 9 * * *" "运行报告"   # 计划一个任务
chimera cron learn                     # 从重复任务中提议 cron（已禁用）
chimera bench                          # 持续进化基准
chimera guard "rm -rf /"               # 预览一次治理裁决
chimera migrate hermes ~/.hermes --apply   # 导入配置 + 技能 + 合并记忆
chimera evolve status / recipe             # 可选模型进化：SFT/DPO 数据 + LoRA 配方
chimera pet new --name Chimi               # 领养一个虚拟伙伴（属性会随时间衰减）
```

参见 **[使用指南](docs/usage.md)**，了解安装、配置以及每条命令的可复制示例。

## 架构

```
chimera/
  core/          智能体循环（ReAct）+ 第 2 层自主（规划、verify-or-revert、监督）
  fusion/        专家组 -> 评审 -> 综合器 + 成本感知路由器
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        内置库 + skill-context 检索
  evolution/     习得技能进化器、经验缓冲区
  governance/    信任内核（allow/warn/block/review）、静态校验器、审计、受治理工具
  orchestration/ 角色、顺序与监督型 crew、MOC 通信
  ecosystem/     元智能体、变更节奏治理、轨迹收集、模型进化
  tools/         原生工具（文件、shell、http）
  integrations/  MCP 客户端（stdio）+ OpenAPI->工具 导入器
  scheduler/     cron（指派 + 自学习）+ SOP 引擎
  migration/     从 Hermes/OpenClaw 导入（配置、技能、记忆合并）
  providers/     LLM 网关（LiteLLM）—— 回退链、凭据池、自定义端点
  interface/     对话式 ChatSession（由 chat、TUI、网关 共享）
  tui/           全屏 Textual 应用
  server/        消息网关 + HTTP 传输（每会话独立）
  eval/          持续进化 + EvoClaw 压力测试 + 日常场景
  cli/           `chimera` 命令（CLI 优先）
```

参见 [docs/architecture.md](docs/architecture.md) 了解完整设计及其所依据的研究。

## 路线图

| 里程碑 | 状态 |
|---|---|
| M0 — 基础（网关、配置、CLI） | ✅ |
| M1 — 第 1 层 + 工具/技能/集成/cron/迁移 | ✅ |
| M2 — LLM-Fusion 引擎 + 成本感知路由器 | ✅ |
| M3 — 第 2 层自主（verify-or-revert） | ✅ |
| M4 — 自我进化（记忆、技能、习得 cron、基准） | ✅ |
| M5 — 治理内核 | ✅ |
| M6 — 第 3 层多智能体团队 | ✅ |
| M7 — 第 4 层自我进化生态 | ✅ |
| M8 — 接口（chat/TUI/网关）、EvoClaw 压力测试、可选模型进化 | ✅ |
| 提供方层 —— 自托管端点、回退链、凭据池、`/model` | ✅ |
| 功能 —— Vision、交付模式、Pets + 预置能力槽 | ✅ |

M7 之后，智能体针对真实的提供方模型进行了强化（在线测试：Fusion、第 2 层 `solve`、日常场景套件、
HTTP 网关、OpenAPI 导入器和 stdio MCP 客户端）。下一步：更深入的大规模持续进化验证、更多提供方集成
（OAuth 登录、凭据池调优），以及可选的 LangGraph 持久化后端。

## 开发

```bash
uv run ruff check .      # lint
uv run mypy chimera      # 类型检查（strict）
uv run pytest -q         # 测试
```

参见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)。
安全问题：参见 [SECURITY.md](SECURITY.md)。

## 社区

来 **[Discord](https://discord.gg/ACvBbrmguV)** 一起聊 —— 欢迎提问、出点子和贡献。

## 许可证

[Apache-2.0](LICENSE)。
