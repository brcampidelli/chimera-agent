---
source_sha256: 1f1c80dd0c5b6b4b6bade6bfbe0cf3d94969fa6ec1e9918d2e186fad3f1a2cd4
---

# Chimera — 架构

本文档将代码库与设计以及所依据的研究联系起来。关于"为什么这样设计"，请参阅
[VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md)。

## 推理核心：LLM-Fusion

`chimera/fusion/`

融合引擎让任务先经过一个模型**面板**（panel），由**评审者**（judge）产出结构化分析（共识 / 矛盾 /
部分覆盖 / 独特洞见 / 盲点），随后**综合器**（synthesizer）依据该分析撰写最终答案
（`FusionEngine`）。它实现了 `SupportsComplete` 协议，因此在任何需要模型的地方——包括在 agent
循环内部——都可以作为即插即用的推理后端。

**成本感知路由器**（`RoutedBackend` + `RoutingPolicy`）让融合保持精挑细选：工具调用轮次交给单一
模型处理（融合本身不做 tool-calling），只有深度 / 高风险的推理轮次才会被融合。灵感来自
OpenRouter Fusion（提升来自*综合*这一步，而不仅仅是模型多样性）和 AURORA-AI（跨异构模型的自适应
预算）。

## Agent 循环与 Tier-2 自治

`chimera/core/`

- `Agent` —— 一个极简的 ReAct / 工具调用循环，带有**显式的会话记录**（状态存在于模型之外）。仅
  依赖 `SupportsComplete` 和 `ToolRegistry`。
- `AutonomousAgent` —— Tier-2：组装带所有权范围的 **Spine** 上下文 → **规划** → 快照 → 执行 →
  **Manager 审查**（生成-验证分离，generate-vs-verify）→ **验证或回滚**（verify-or-revert）→
  带反馈重试，并将每次尝试记录到经验缓冲区。
- `WorkspaceGuard` —— 文本文件的快照/恢复，是 verify-or-revert 背后的机制。
- `CommandVerifier` —— "可执行证据"（exit 0 == 成功）。

### 应对持续演进中的性能退化

这是一个尚未解决的问题（引自 *Agentic Software*，`2606.05608`）：性能会从孤立任务上的 >80% 跌至
持续演进场景下的约 38%——原因是长跨度上下文加上错误的层层传播。Chimera 的对策，每一项都有文献
依据：

| 对策 | 位置 | 依据 |
|---|---|---|
| 外部化状态（放在会话记录/工作区，而非 LLM 上下文里） | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| 带所有权范围的上下文（Spine） | `core/spine.py` | Spec Growth Engine `2606.27045` |
| 生成-验证分离监督 | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| 验证或回滚（Verify-or-revert） | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| 经验缓冲区（把失败当作负样本） | `evolution/experience.py` | HORIZON `2606.28279` |
| 团队内的消息合并 | `orchestration/comms.py` | MOC `2606.02359` |
| 持续演进基准测试 | `eval/continuous.py` | EvoClaw 问题陈述 |

## 记忆与自我演进

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** —— 分层的记忆项（working / episodic / semantic / persona），支持
  `ADD / UPDATE / DELETE / NOOP`（`remember`）以及 `merge` 去重（Memory-R1，`2606.14502`）。
- **Skill evolver** —— `SkillEvolver` 从一次成功中提炼出可复用的 `LearnedSkill`，对其进行测试，
  只有通过测试才会保留（提出 → 测试 → 保留/丢弃）。学到的技能是**提示词模板，而非可执行代码**
  ——在代码层面的自我修改之前，自主生成它是安全的。精炼过程会依据失败案例改进模板（VIBEMed
  `2606.15504`）。
- **自学习的定时任务（crons）** —— `CronLearner` 检测重复出现的任务并提议新增 cron
  （`created_by=agent`，在人工批准前保持**禁用**状态）。
- **持续演进基准测试** —— 让一连串任务依次通过求解器，并报告性能退化情况（整体通过率、前半段
  与后半段对比、最长连胜纪录）。

## 治理与安全

`chimera/governance/`

一个能自我改进的信任内核（AgentTrust v2，`2606.08539`）：

- `TrustKernel.evaluate(action)` → **允许 / 警告 / 阻止 / 复核**。词法层面的 `RuleSet` 以确定性
  方式处理固定特征的威胁；可选的**语义评审器**处理意图判断；蒸馏出的规则会让成本随时间降低。
  不变量：**绝不硬性阻止一个良性动作**。
- `SkillValidator` / `ScheduleValidator` —— 面向自我修改的**受限、可静态检查的编辑面**
  （AutoMegaKernel `2606.09682`）：不安全的提案在运行前就会被拒绝。
- `AuditLog` —— 只追加写入的 JSONL，记录各项决策与演进变更。
- `GovernedTool` / `govern_registry` —— 包装任意工具，使其执行受到管控；无需改动即可与现有
  agent 循环组合使用（`chimera ... --guard`）。

### 污点层（prompt-injection 遏制）

叠加在内核之上——启发式、诚实、且从不作为硬边界（沙箱才是硬边界）：

- `TaintLedger` + `LedgeredTool`（`ledger.py`、`ledger_tool.py`）—— 按运行维护的能力台账。一次
  抓取（fetch）会给其内容打上污点；消费了带污点内容的写入/执行操作会**升级为复核**
  （`assess_action`）。不可信的抓取内容会以**数据围栏**（data-fenced）形式返回，并剥离聊天模板
  控制 token（`sanitize.py`）；带污点运行产生的持久制品会保留 `tainted` 溯源标记，防止污染借
  "干净"的记忆/技能洗白自己。
- `AggregateMonitor`（`aggregate_monitor.py`）—— 高一层的监控器：基于每个子 agent 的能力事件，
  捕捉单个 agent 监控器看不到的**拆分式攻击流**（agent A 抓取不可信内容，agent B 执行或**外泄**
  它）。
- `check_drift`（`drift.py`）—— 一份由可执行需求（`defines`/`contains`/`absent`/`command`）组成
  的 `Spec`，既充当 `solve --verify` 的事实依据，也是项目编排器判断"完成"的权威依据（详见下
  文）。否定式检查在无法扫描的文件上会失败并采取保守（fail-closed）策略。
- `QuarantineTool` + 自适应白名单（`quarantine.py`、`allowlist.py`）—— 一个类似 dual-LLM/CaMeL
  的隔离读取器，以及一份随污点状态自适应收紧的工具白名单。

## 多智能体团队（Tier 3）

`chimera/orchestration/`

- `Role` + `RoleAgent` —— 角色专精（CrewAI 风格）。
- `SequentialCrew` —— 各角色按顺序执行，每个角色都能看到**合并后**的前序输出，并可写入共享
  记忆。
- `SupervisorCrew` —— 多个 worker 并行处理任务，输出被合并，再由一个 supervisor 进行综合
  （CAPRA 风格的 `parallel_review`，`2606.18976`）。
- `consolidate` —— MOC 式消息合并让团队上下文保持精简（`2606.02359`）。

## 自我演进生态系统（Tier 4）

`chimera/ecosystem/`

- `MetaAgent` —— 设计/构建/评估专用 agent（agent 构建 agent）。借鉴 Meta-Agent Challenge
  （`2606.04455`）的两项防护：**工具隔离**（被设计出的 agent 只能使用一份受限的工具白名单）和
  **隐藏测试隔离**（可见测试通过 + 隐藏测试失败 ⇒ 判定为疑似 reward-hacking，不计为成功）。
- `ChangeQueue` —— 管控变更的*节奏*（FIFO 合并队列 + 批次上限），而非管控 agent 的数量
  （"Govern the Repository"，`2606.28235`）。
- `TrajectoryCollector` —— 记录 (prompt, response, outcome) 三元组，并导出 **SFT / DPO** 数据
  集。真正的微调是**可选且外部进行的**——Chimera 只负责采集，不负责训练。

## 成本经济学与委派层级

`chimera/orchestration/`（hierarchy、cascade、budget、receipts、envelope_verify）

只有当委派比自己动手更划算时才会真正划算，而这一判断是**经过测量、而非凭空断言**的：

- `HierarchicalOrchestrator` —— 分解 → 派发有预算限制的 worker → 逐一验证结果 → 综合。适合
  "读取型"任务的会被扇出委派；一个明显很小的子任务则由受信任的顶层模型直接就地作答。
- `CascadeBackend` —— 弱模型 → 关卡 → 中等模型 → 关卡 → 融合，只有当某一档的答案未能通过廉价的
  验收关卡时才会往上升级。**route log** 会记录每一跳，因此成本是**所有尝试过的跳数之和**，而不
  仅仅是最终被采纳的那一跳——升级本身也是要付费的。
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` —— 在后端层面按 worker 强制执行的 token
  硬上限。
- `EnvelopeVerifier` —— 模式校验 → 验收标准 → 概率性的**抽样核查**（对照原始制品评估摘要的
  忠实度）；抽样核查失败会触发复问，复问结果也会被重新审计。
- **委派存证**（`receipts.py`）—— 每一次委派都会在同一行里记录其实测的 token/成本，**以及若改
  为就地完成的反事实成本**，按各模型自身的价格计价（未知模型 → `None`，绝不臆造）。编排器自身
  分解/综合的开销也会被计量，因此 `summarize_delegations`（`chimera delegations`）报告的是**可
  审计**的净节省，而 `cascade-bench` 报告的是成本的**尾部分布**（p50/p95/p99），而不只是均值。

## 自我演进飞轮

`chimera/evolution/`

这是一种从不触碰模型权重的"训练"——由适应度（fitness）信号驱动、无梯度、且可回滚：

- `EvolutionContext` —— 一个共享的组装体（experience、trajectories、memory、auto-evolver、
  skill cards、playbook），它让"学习"成为整个 agent *技术栈*的属性，而不只是 `solve` 命令的
  属性。
- Skill card 加上 **GEPA** 精炼、ACE **playbook**，以及一套 `SkillLifecyclePolicy`，依据**实测**
  的使用/成功统计数据来晋升/降级一项技能（新技能诞生时状态为 `provisional`）。
- **diff-gate**（差异关卡）—— 一次"空洞的成功"（验证器通过了，但工作区的 diff 是空的）既不会
  铸造出新技能，也不会写入记忆；飞轮只从确实发生过的工作中学习。
- **transfer-gate**（迁移关卡，`eval/transfer.py`）—— 一件调优后的制品只有在留出集（holdout）
  上依然成立时才会被晋升，以防范负迁移。`maturity.Scorecard.weakest()` 就是优化目标：循环始终
  瞄准最薄弱的能力项。回归只有在出现**统计显著**的下降时才会自动回滚（依据的是置信区间，而不
  是单个数据点）。

每一次默认值的翻转，背后都有一次**预先登记**的配对 A/B 实验把关（`bench/`），无论赢输都会发布
——不会为了凑出显著性而反复重掷骰子。

## 项目自治（从头到尾）

`chimera/orchestration/project.py`

`ProjectOrchestrator` 让整个项目对照一份 `Spec` 运行：任务图（一个带 `depends_on` 的 Kanban 式
DAG）→ 每张就绪的卡片被求解（借助上文的演进上下文）→ 通过 `check_drift`（判断"完成"的唯一权
威）**对照 Spec 验收** → 未满足的需求会生成后续卡片，如此循环，直到 Spec 完全对齐，或被预算 /
最大迭代次数 / 人工检查点叫停。风险较高的步骤（`risk: high` —— 部署 / 迁移 / 删除）会**暂停以
等待人工批准**；整个运行过程是持久化且可续跑的。

## 横切关注点

- **Providers**（`providers/`）—— 基于 LiteLLM 的统一、与具体厂商无关的网关；密钥可以放在
  `.env` 中，并会被导出到环境变量，以便 LiteLLM 能读取到它们。
- **Tools**（`tools/`）—— 原生工具原语；工具元数据是实例属性，这样动态生成的工具（OpenAPI/MCP）
  也能正常工作。
- **Integrations**（`integrations/`）—— MCP 客户端（可选 extra `mcp`）+ OpenAPI→tool 导入器 +
  连接器注册表。
- **Scheduler**（`scheduler/`）—— 定时任务（crons）+ 事件 SOP；时间是被注入的，以便测试保持
  确定性。
- **Migration**（`migration/`）—— 导入配置 + 技能，并**合并**来自 Hermes / OpenClaw 的长期记
  忆，去重且不具破坏性。

## 测试理念

每个子系统都用**伪后端**（fake backends）做了单元测试——确定性、不联网、不需要密钥。真正会
调用 LLM 的命令，则针对其"无密钥"失败路径做了冒烟测试（smoke test）。质量关卡（`ruff` +
`mypy --strict` + `pytest`）在 CI 中于 Python 3.11 和 3.12 上运行。
