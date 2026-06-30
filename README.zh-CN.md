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

> **状态：** 早期开发（0.1.x）。完整的构建计划（M0–M7）已实现 —— 四个能力层级 + 融合引擎 + 多层自我进化
> + 治理内核 —— 另加一个**闭合的行为学习回路**、一个**运维层**（看板 + worker lanes、SDLC crew、声明式
> 回路 DSL）、**执行隔离**（Docker 沙箱 + git worktrees），以及它据以设计的**论文技术**（HORIZON、VIBEMed、
> Spec Growth、AgentTrust v2、AutoMegaKernel、Meta-Agent、MOC）。
> 332 个测试（外加可选的在线集成）· `mypy --strict` 通过 · `ruff` 通过。

---

## 为什么选择 Chimera

现有框架各自只在一个维度上强：Hermes/OpenClaw 能进化技能但使用单一模型；CrewAI/LangGraph 编排得好但不会学习；
TrustClaw/NemoClaw/ZeroClaw 带来安全/沙箱但不会进化。**Chimera 把这四者结合起来：**

- 🧬 **融合即推理** —— 专家组→评审→综合器引擎是推理核心，而非附加件。提升来自*综合*步骤本身，而不仅是模型多样性。
- 🪜 **一条进阶路线上的四个能力层级** —— 增强工具 → 单任务自主 → 多智能体团队 → 自我进化生态。
- ♻️ **一个闭合的多层自我进化回路**，明确对抗持续进化退化（外部化状态、抗漂移上下文、verify-or-revert、回灌到规划中的经验缓冲区）。
- 🛡️ **同样会自我改进的治理内核** —— allow/warn/block/review，带有经过静态校验的自我修改面和受保护的先例。

## 功能

**推理与自主**
- **LLM-Fusion 引擎** —— 与厂商无关的前沿 + 开源模型专家组、一个揭示共识/矛盾/盲点的评审，以及一个综合器；一个**成本感知路由器**只在划算时才融合（工具调用回合保持单模型）。
- **第 2 层自主** —— 规划 → 执行 → Manager 评审 → **verify-or-revert**（工作区快照/恢复 + 命令校验器），并带 **git worktree 隔离**（`solve --isolate`）—— 改动只有验证通过才落地。
- **SDLC 生命周期 crew**（`chimera lifecycle`）—— 预装的 **plan → build → test → review** 流水线，在 test 阶段做 verify-or-revert。
- **多智能体团队** —— 角色专精、顺序与监督型 crew、MOC 消息整合、共享记忆、并行评审。

**自我进化与治理**
- **闭合行为回路** —— 过去的失败喂给 planner（教训）、验证通过的成功自动写入记忆、重复任务自动进化出一个经校验且 smoke-测试的技能 —— 全部由 verify-or-revert 把关。另有持续进化基准和 EvoClaw 朴素 vs 受护 压力测试。
- **分层记忆** —— working / episodic / semantic / persona **+ 一个 graph 层**（`memory graph`），按实体而非仅关键词召回事实。
- **可选的模型进化** —— `solve` 收集轨迹；`evolve` 整理为 SFT/DPO 数据集并生成可运行的 LoRA 配方。训练保持外部/可选。
- **治理内核** —— allow/warn/block/review（词法规则 + 可选语义评审，带规则蒸馏与**受保护的先例库**）、自我修改面的静态校验器、仅追加审计日志、受治理工具、**四角色变更模型**，以及 **spec↔代码 漂移闸**（`chimera drift`）。

**模型提供方**
- **任意模型，一个接口** —— 通过 LiteLLM 与厂商无关（用 `provider/model` 标识访问 100+ 模型）；为 OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek 提供一等密钥。
- **自托管且具弹性** —— 为 **Ollama/vLLM** 提供自定义端点（`CHIMERA_API_BASE`）、跨模型**回退链**、带轮询轮换的**凭据池**、实时 **`/model`** 切换，以及对重复推理回合的 **prompt 缓存**（`CHIMERA_CACHE`）。

**编排、接口与集成**
- **看板 + worker lanes**（`chimera kanban`）—— 任务板（backlog → doing → review → done），卡片被分派到 `solve` 或 `crew` lane；`kanban learn` 把重复任务变成卡片。
- **Loop Engineering**（`chimera workflow`）—— 用 YAML 编写自主回路（`use` 各能力的步骤，带 `when` 条件与 `repeat`/`until` 循环）。
- **接口** —— 一个 `chat` REPL、一个全屏 **TUI**（Textual），以及一个每会话独立对话（与记忆）的 **消息网关** HTTP 服务器。
- **执行沙箱** —— 在本地或隔离的 **Docker** 容器中运行 shell 工具（`CHIMERA_SANDBOX=docker`）。
- **集成** —— 一等的 **MCP** 客户端（stdio）+ **OpenAPI/REST → 工具** 导入器；**cron**（人工指派与自学习，带确认）；从 Hermes Agent / OpenClaw **迁移**配置/技能/长期记忆。

**内置附加项**
- **Vision**（粘贴图片）、**交付模式**（精炼成果物）和一个 **Pet** 伙伴 —— 另加用于网页搜索、图像生成、TTS/语音等的预置凭据槽（`chimera features`）。

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
chimera deliver "一个计划" -o plan.md   # 交付模式：生成精炼成果物
chimera fuse "PROMPT" --show-panel     # LLM-Fusion：专家组 -> 评审 -> 综合器
chimera solve "任务" --verify "pytest -q" --rubric --isolate   # 第 2 层：verify-or-revert（+ 级联量表评审），git worktree 隔离
chimera lifecycle "任务" --verify "..."   # SDLC crew：plan -> build -> test -> review
chimera workflow flow.yaml             # 运行声明式回路（Loop Engineering）
chimera crew "任务" --mode supervisor  # 第 3 层多智能体 crew
chimera meta "一个用于 X 的智能体"          # 第 4 层元智能体：设计一个专用智能体
chimera kanban add/board/run/learn     # 带 worker lanes 的任务板（solve/crew）
chimera drift spec.yaml                # spec<->代码 漂移闸（漂移时退出 1）
chimera memory add / graph             # 经整理的长期记忆 + 实体-关系图
chimera cron add / learn               # 计划任务（指派 + 自学习，带确认）
chimera bench                          # 持续进化基准
chimera migrate hermes ~/.hermes --apply   # 导入配置 + 技能 + 合并记忆
chimera evolve status / tune / recipe   # 可选进化：规格元搜索（tune）、SFT/DPO 数据 + LoRA 配方
chimera pet new --name Chimi           # 领养一个虚拟伙伴
```

参见 **[使用指南](docs/usage.md)**，了解安装、配置以及每条命令的可复制示例。

## 架构

```
chimera/
  core/          智能体循环（ReAct）+ 第 2 层自主（规划、verify-or-revert）+ git worktree 隔离
  fusion/        专家组 -> 评审 -> 综合器 + 成本感知路由器
  memory/        working / episodic / semantic / persona + graph 层 + Memory Manager
  skills/        内置库 + skill-context 检索
  evolution/     习得技能进化器、自动进化钩子、经验缓冲区
  governance/    信任内核（规则 + 评审 + 受保护先例）、静态校验器、漂移闸、四角色模型、审计
  orchestration/ 角色、顺序/监督型 crew、MOC 通信、SDLC 生命周期 crew
  ecosystem/     元智能体、变更节奏治理、轨迹收集、模型进化
  kanban/        任务板 + worker lanes（分派到 crews / solve）
  workflow/      声明式回路 DSL（Loop Engineering）
  tools/         原生工具（文件、shell、http）
  sandbox/       执行后端（local / docker 隔离）
  integrations/  MCP 客户端（stdio）+ OpenAPI->工具 导入器
  scheduler/     cron（指派 + 自学习）+ SOP 引擎
  migration/     从 Hermes/OpenClaw 导入（配置、技能、记忆合并）
  providers/     LLM 网关（LiteLLM）—— 回退、凭据池、自定义端点、prompt 缓存
  interface/     对话式 ChatSession（由 chat、TUI、网关 共享）
  tui/  server/   全屏 Textual 应用 · 消息网关 + HTTP 传输
  eval/          持续进化 + EvoClaw 压力测试 + 日常场景
  cli/           `chimera` 命令（CLI 优先）
```

参见 [docs/architecture.md](docs/architecture.md) 了解完整设计及其所依据的研究。

## 路线图

| 里程碑 | 状态 |
|---|---|
| M0–M7 — 四层能力 + 融合 + 自我进化 + 治理 | ✅ |
| M8 — 接口（chat/TUI/网关）、EvoClaw 压力测试、可选模型进化 | ✅ |
| 提供方层 —— 自托管端点、回退、凭据池、`/model`、prompt 缓存 | ✅ |
| 闭合行为回路 —— 经验→planner、自动记忆、自动技能（受治理） | ✅ |
| 运维编排 —— 看板 + worker lanes、SDLC crew、Loop DSL | ✅ |
| 执行隔离 —— Docker 沙箱 + git worktrees | ✅ |
| 论文技术 —— HORIZON · VIBEMed · Spec Growth · AgentTrust v2 · AutoMegaKernel · Meta-Agent · MOC | ✅ |
| 论文技术（II）—— MemGate · 多因子记忆价值 · Data Recipes · OpenClaw-Skill · SkillAdaptor · DailyReport · OpenJarvis 规格搜索 | ✅ |

下一步：更深入的大规模持续进化验证、提供方 OAuth 登录，以及可选的 LangGraph 持久化后端。
模型训练（LoRA/DPO）按设计保持外部/可选。

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
