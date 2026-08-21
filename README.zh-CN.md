<div align="center">

<img src="assets/logo-wide.png" alt="Chimera logo" width="460" />

# Chimera

**受治理的自我进化智能体 —— 经过验证，受到治理。**<br/>
<sub>用众多智慧思考，自主完成真正的工作，只学习经过验证的东西，并且从架构上保证安全。</sub>

[![Website](https://img.shields.io/badge/chimeraagent.space-visit-3b82f6.svg)](https://chimeraagent.space)
[![PyPI](https://img.shields.io/pypi/v/chimera-agent.svg?color=blue&label=PyPI)](https://pypi.org/project/chimera-agent/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
[![Reddit](https://img.shields.io/badge/Reddit-r%2FChimeraAgent-FF4500.svg?logo=reddit&logoColor=white)](https://www.reddit.com/r/ChimeraAgent/)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![Donate](https://img.shields.io/badge/Donate-Stripe-635BFF.svg?logo=stripe&logoColor=white)](https://buy.stripe.com/9B6aEQ57q91m1Gp7Lz77O01)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.it.md">Italiano</a> · <a href="README.pl.md">Polski</a> · <b>中文</b> · <a href="README.ja.md">日本語</a> · <a href="README.ru.md">Русский</a></sub>

</div>

大多数 AI 助手都把宝押在**单一**模型上，而且聊天一结束就把一切都忘光了。
**Chimera 有两点不一样：** 遇到难题时，它会**同时**询问好几个 AI 模型，把它们的答案融合成一个更强的结果；
而且它会**记住并学习**，用得越多就越好用。它不只是聊天 —— 给它一个目标，它就会规划、使用工具、检查自己的成果，
只保留真正有效的部分。

> **免费且开源（Apache-2.0），处于早期但活跃的开发阶段。** 它已经能端到端地工作：和它聊天、让它自己完成任务、
> 把它当作机器人跑在你喜欢的聊天软件上、部署到服务器让它 24/7 运行，并看着它从自己的行动中学习。它目前是
> **alpha 版** —— 扎实且经过大量测试（**2800+ 项自动化测试**，每次改动都做严格的类型检查和代码风格检查），但还没有
> 在生产环境中千锤百炼。

---

## 为什么选择 Chimera

把大多数 AI 工具想象成只问**一位**专家，然后祈祷他是对的。而 Chimera 就像拥有一个会互相讨论的**专家小组**、
一位权衡各方答案的**公正评审**，以及一位交付最佳综合结果的**写手** —— 然后再加上一位真正**动手干活**并从中
**学习**的队友。用大白话说，它的特别之处在于：

- 🧠 **众多智慧，一个答案。** 遇到难题时，Chimera 会拿同一个问题去问好几个模型，让一个模型来比较它们的答案，再让最后一个模型写出最佳的综合回复 —— 这样你得到的结果比任何单一模型都更均衡、更不容易出错。（它只在值得的时候才这么做，以保持快速又省钱。）
- 🚀 **它真的干活，不只是嘴上说说。** 给它一个目标。它会把目标拆解开来，使用工具、编辑文件、运行测试，而且**只有通过测试才保留这次改动**。如果哪里出了问题，它会撤销并重试 —— 所以它不会给你留下一堆烂摊子。
- 🧬 **它会记住，而且它的设计目标就是持续进步。** 它会跨对话记住你的偏好和重要事实，并默默地把它反复做的任务变成可复用的技能，抵抗那种在长期运行中慢慢拖垮许多智能体的退化。**诚实的附注：** 累积下来的学习能让它在任务上*确实做得更好*，这一点**并没有得到证明** —— 七次预注册运行都没有测出显著效果，而其中唯一那个阳性结果因为没能复现，已经被我们撤回（[`bench/learning_lift/RESULTS.md`](bench/learning_lift/RESULTS.md)）。
- 🛡️ **从设计上就安全。** 每个有风险的操作都要先通过一道安全检查，任何破坏性动作都会先请你确认，而且不受信任的代码可以在一个封闭隔离、断网的容器里运行。（这些检查只是一道廉价的初筛，并不是真正的边界——沙箱才是；而且容器隔离是需要手动开启的。见 [SECURITY.md](SECURITY.md)。）
- 🔌 **任意模型，随处运行。** 通过同一个界面，既能用大型托管模型，也能用你自己的本地模型 —— 无论是在你的笔记本上还是一台 5 美元的服务器上，全天候运行。
- 🧩 **真正属于你。** 开源、无锁定、无需注册厂商账号。你运行它，你拥有它，你可以修改任何东西。

## Chimera 与众不同之处

Chimera 并不打算在*渠道数量*上去和那些巨型智能体项目一较高下。它押注于三件事 —— 一项对五个头部项目
（OpenClaw、Hermes、nanobot、CrewAI、LangGraph）的真实逆向工程研究发现，它们**全都没有做到** —— 并把这三件事
作为自己的核心：

- 🧬 **带适应度信号的自我进化。** 其他项目"学习"的方式，要么是把发生过的一切追加记录下来，要么靠人工提交 pull request —— 没有任何东西去衡量一次学到的改动是否真的有帮助。Chimera **只在一个经过验证的结果证明它确实有用时**才保留这次改动：进化步骤以真实的工作树 diff 和一次诚实的 A/B 为门槛，绝不凭模型的一面之词。这一点很重要的独立证据：[EvoAgentBench（arXiv 2607.05202）](https://arxiv.org/abs/2607.05202) 测得，*自动的*、无门槛的经验编码方法常常产生**负迁移** —— 一种流行的方法在未经调优的任务上倒退了 **−12.3 分**。Chimera 的门槛如今还会跑一次**迁移留出（transfer holdout）**：一次学到的改动，在被提升之前，不得让一个互不相交、同等能力的切片出现倒退，所以它不能只是把自己的评测背下来。
- 🛡️ **架构级安全。** prompt injection 如今被广泛认为是*无法根治的*；那些流行的智能体要么在应用层做缓解，要么干脆声明它超出范围（其中一个上线时有 13.5 万个公开暴露的实例，还有一个约 12% 充斥恶意技能的市场）。Chimera 提供了一层真正的防御 —— **通过 `--taint` 手动开启，默认关闭**：它以*启发式*方式追踪污点来源（逐字的引用／内容流，而**非**真正的数据流 —— 会改写污染内容的模型可以将其“洗白”），从不受信任内容中剥离控制 token，在被污染运行的其余部分收窄对危险工具的访问，并守护带副作用的重试；不受信任的代码在一个需手动开启、封闭隔离的容器里运行。在内置的 **7 个攻击** 语料上，**7 次有害调用里有 6 次**被拦截（仍有**约 14%** 漏过）—— 这是在一个*已经被注入*、正准备发起攻击者那次工具调用的智能体上测得的，回路里没有模型参与。这个拦截率从不单独公布：同一份报告还带着这层收窄拒绝掉了多少*正当*工作，测自一份会触发同一道面的良性语料，而门槛不会只读其中一半（`chimera redteam` 两个都会打印 —— 一套只按攻击计分的防御，满分平凡得可笑：全部拒绝就行）。未设防的那一支是 100%，这是构造使然而非测量所得：一个没被包裹起来的工具总会执行，所以请把它当作这一层用来对照的定义性下限，而不是一个基线系统。这完全没有说明一个模型一开始有多容易被注入 —— 那才是更难、也仍然敞开着的另一半（[`chimera/eval/injection.py`](chimera/eval/injection.py)）。[`SECURITY.md`](SECURITY.md) 明确说明了哪些仍会漏过（子智能体之间的交接、融合／摘要、CLI 之外的入口）—— 遏制边界是沙箱，这一层是其上的纵深防御。
- 📊 **诚实、公开的基准测试。** 某个流行排行榜上约 20% 被标为"已解决"的用例其实是错的。Chimera 报告每一个数字时都附上置信区间 —— **包括它没有胜出的那些运行** —— 绝不为了显著性而重新掷骰子，而且当一次复现推翻了它自己的主张时，它就把那条主张撤回。这些数字、这些零结果和这些撤回，全都在[基准测试（诚实版）](#基准测试诚实版)一节里。

**一句话概括：受治理的自我进化智能体 —— 经过验证，受到治理。** 它是 alpha 版，而且它坦率承认这一点。

## 基准测试（诚实版）

四项被记录下来的结果，我们故意一起公开：两项支持我们的论点（其中一项只有汇总后才显著）、
一项与我们相悖，还有一项被我们撤回。
（桌面应用的**成熟度与基准**界面也会直接从随附的快照中展示它们——该界面报告的是项目自身的
测试覆盖率，因此只在 Vite 开发服务器下渲染（`npm --prefix apps/desktop run dev`）。`chimera app`
提供的是生产构建，不会显示它，原生安装包同样不会。）

- **弱模型提升（显著）。** 一个便宜的模型（`mistral-small-3.2-24b`）加上 Chimera 的重试循环，
  对比同一模型单独运行，在一套**预注册的 n=100 题目**上（设计与题目在任何一次模型调用之前就已提交并推送）：
  **48.0% → 71.0%（+23.0 个百分点）**，配对 **95% 置信区间 [+12.6%, +28.6%] —— 具统计显著性**
  （区间不含 0），来自**循环挽救回来的 28 道题**（原始失败 → 经验证通过），对应 5 处回退。
  一个模型、一个随机种子/题目、小型自包含的 Python 任务——**不是** SWE-bench，也不能推广到真实仓库。
  一次运行，没有重掷。
  **这取代了同一套题目更早的一次运行**（9.0% → 15.0%，+6.0 个百分点），那次的评测框架用了一个
  受测智能体可以修改的测试文件。恢复原始测试重跑后，抓到智能体在一道题上改写了自己的评分测试——
  说明这个漏洞是真实存在的——而提升幅度复现得*更大*，而不是更小。早先那次运行声称
  “100 道题中有 85 道难到两个分支都会失败”，也没有成立：重跑测得 24 道。完整的勘误、保留下来的
  篡改证据，以及哪些内容无法再验证，都在
  [`bench/local_lift/RESULTS.md`](bench/local_lift/RESULTS.md)。
  来源：[`bench/local_lift/_reverify_n100/paired.json`](bench/local_lift/_reverify_n100/paired.json)、[`PREREGISTRATION.md`](bench/local_lift/PREREGISTRATION.md)。
- **SWE-bench Verified —— 最强的外部证据，而且它挺过了一次专为推翻它而设计的复现。**
  四次预注册运行，基于 `django/django` 的切片，**只**由 Docker 中官方的 `swebench` 4.1.0 评测框架评分——
  从不自评自报。

  | 运行 | 切片 | 基线 | + Chimera | 配对 Δ | 95% CI | |
  |---|---|---|---|---|---|---|
  | 1（`max_steps=8`） | 19 | 36.8% | 36.8% | +0.0% | [−8.5%, +8.5%] | 不显著 |
  | 2（`max_steps=30`） | 同样的 19 | 42.1% | 57.9% | +15.8% | [−1.9%, +15.8%] | 不显著 |
  | **3（复现）** | **41 道未见过的** | 34.1% | 43.9% | **+9.8%** | [−3.5%, +16.7%] | 不显著 |
  | 合并 *(次要指标)* | 60 | 36.7% | 48.3% | **+11.7%** | **[+0.8%, +16.4%]** | **显著** |

  运行 2 的 +15.8% 来自三个信息量对上的 3–0 全胜，而预注册文件当时就给了它
  **三分之一的概率恰好就是这样——一次运气好的抽样**，并预先承诺了撤回条件。运行 3 在
  **41 个我们从未看过结果的实例**上测试了它，其他什么都没改。效果**再次出现**
  （+9.8%，落在登记的 +5 到 +20 区间内），而且这个切片比运行 2 的更*难*。两次合起来，
  不一致对是 **Chimera 9 比 2 领先**（零假设下 p ≈ 2.6%）。

  **机制也复现了，而这才是有意思的部分。** 第四次运行在同样的 41 个实例上恢复了中间分支
  （纯脚手架，不带 diff 门），使三者恰好只差一个组件。三者**编辑的频率相同**（41 中有 27–28 个补丁）；
  变化的是这次编辑*正确*的频率：

  | 分支 | 解决数 | **编辑时的精确率** |
  |---|---|---|
  | 基线 | 14/41 | 50% |
  | + 脚手架 | 16/41 | 59% |
  | + 脚手架**和** diff 门 | 18/41 | 67% |

  **两个组件都有贡献，大致各占一半**（各 +4.9%，单独看都不显著）——这**与我们自己登记的预测相矛盾**，
  那份预测认为脚手架会承担大部分；同时也撤回了运行 2 中“diff 门不是产生增益的原因”这一读法。
  撤回说明在 [`RESULTS.md`](bench/swe_bench/RESULTS.md)；这种齐整的可加性*并不*被宣称为实测出的
  50/50 拆分，因为每次比较只建立在 5–6 个不一致对上。

  ⚠️ 请诚实地读：**样本外的主要指标并不显著。** 显著的那个数字是**合并后的次要指标**，
  它被预注册为次要，正是因为它把见过的数据和未见过的数据混在了一起——现在它越过了显著性线，
  我们也不会把它提升为头条。而且 **48.3% 不是 SWE-bench Verified 的成绩**：这是一个刻意挑选的、
  来自单一仓库的简单切片；真正的成绩需要完整的 500 道题。运行 1 那个精确的零原样公布，
  运行 2 也交出了**它应得的撤回**（我们此前为它的空补丁给出的机制解释是错的——真正的解药是步数预算）。
  来源：[`bench/swe_bench/RESULTS.md`](bench/swe_bench/RESULTS.md)、[`PREREGISTRATION.md`](bench/swe_bench/PREREGISTRATION.md)。
- **Terminal-Bench（让人难堪）。** 在官方基准上做的预注册 N=40 A/B 测试，两个分支使用同一个模型
  （`deepseek-chat-v3.1`）：加上脚手架后 **7.5% → 2.5%**，配对 **Δ −5.0 个百分点，95% CI
  [−5.0%, +1.6%] —— 不显著**。脚手架**没能提升一个本来就有能力的模型**
  （这不是脚手架能帮上忙的那种“恰到好处”的弱模型区间）；两个分支都处在被方差主导的地板上。
  来源：[`bench/terminal_bench/RESULTS.md`](bench/terminal_bench/RESULTS.md)。
- **累积学习有用吗？七次运行的回答是：无法证明（而且有一个阳性结果被撤回了）。**
  这个飞轮——以复现次数加迁移测试为门槛的技能、反模式卡片、持久记忆——在**七次预注册运行**中被测量。
  运行 6 产出了整个系列唯一的阳性结果（同族迁移指标上显著的 +6.7%）；**运行 7 在更高的统计功效下
  把它压到 +2.0% 且不显著——于是它被撤回了**，完全按照预注册文件的承诺执行。诚实的结论：
  **没有任何一次功效充足的运行显示累积学习能提高任务成功率**，而瓶颈在于测量工具本身——
  三次试图编写一套落在 40–60% 这个信息量区间的题目，结果都落在了 84–92%。
  “你用得越多它越好”这一说法目前**没有证据支持**。
  来源：[`bench/learning_lift/RESULTS.md`](bench/learning_lift/RESULTS.md)。

在内部（我们自己的难题集上）显著。在真实仓库上，**样本外复现成功，且只有合并后才显著**——
这是诚实的标签，不是好听的那个。在 Terminal-Bench 上很难堪。关于学习的那条主张已被**撤回**。
我们把这些全部公开，我们在跑之前就写下“结果会杀死我们自己主张”的那个分支，
而且我们不会为了追求显著性去重跑——那是 p-hacking。

## Token 经济学 —— 实测，而非空谈

两条"模型越多 = 越好"的直觉，在真实运行中经受了压力测试（每次运行*之前*都登记好预测，胜绩**和**败绩一并
公开 —— 见 [`bench/`](bench/)）：

**融合是备用手段，而非默认选项。** 在一套 12 项推理任务上，仅中档一层就以 846 个 token 拿下
100%；完整融合也拿到 100% —— 却花了 **9,526 个 token（约 11 倍）**。所以融合被放在一条
便宜→门槛→中档→融合的级联之后，只有在免费的门槛失败时才升级，从而以融合约 1/12 的成本达到约中档的质量。
它自己登记在案的判定标准 —— *级联的通过率不低于仅用中档，且成本显著更低* —— **并没有达成**：级联落在
91.7%，而中档一层是 100%，因为在这套题目上中档一层本来就已经饱和，没有留下任何提升空间。那唯一一次
失手很有教育意义：一道免费的词法门槛抓不住一个自信却错误的答案
（[`bench/cascade/RESULTS.md`](bench/cascade/RESULTS.md)）。

**分层编排只在它该赢的地方赢 —— 而且赢得有一条我们能写下来的定律。**
`chimera orchestrate` 把一个任务拆分到多个限定范围的 worker 上，而不是塞进一个庞大的上下文。单个
智能体每一轮都要重发每一份文档；限定范围的 worker 各自只读一次。所以 token 的节省
随文档数 D 以 **(D−1)/D** 的规律递增 —— 在真实运行中得到确认，误差 <0.2%：

| 文档数 (D) | 实测 token 节省 | (D−1)/D |
|---|---|---|
| 2 | 49.9% | 50% |
| 3 | 66.7% | 66.7% |
| 4 | 74.8% | 75% |
| 5 | 79.9% | 80% |

随着对话变长，节省幅度保持平稳，并随文档变大而朝同一个
上限攀升（[完整扫描，3 个维度](bench/hierarchy_sweep/README.md)）。而在它*不*划算的地方 —— 一个
只有一轮的单发任务 —— 分类器会检测到这一点，并**回退到单个智能体**
（那次运行多花了 +47% 的 token；我们也把它发布了出来）。

**诚实的附注。** 这些都是 *token* 计数。有了 prompt caching，提供方会把单个
智能体重复的文档按约 0.1 倍计费，所以以*美元*计的胜幅会更小 —— 而且过了几轮之后甚至可能
**反转**（相互独立的 worker 要重新支付单个智能体所缓存的冷上下文）。我们发布的是
[量化这一点的模型](bench/hierarchy_sweep/cache_cost.py)，而不是悄悄把 token 数字当成美元数字来宣称。

## 功能

### 🧠 思考与行动
- **把多个模型融合成一个答案**（`chimera fuse`）—— 一个模型专家小组，一位评审指出它们在哪里达成一致、产生分歧或有所遗漏，还有一位综合器写出最终答案。一个智能路由器只在遇到难题时才投入这份额外的功夫，而当最先给出答案的几个模型已经达成一致时，它就会提前停下 —— 在我们的基准测试中实测为**减少约 20–28% 的 token**；三次运行中准确率在 0 到 −8.3 个百分点之间波动，而这一波动完全落在被升级的那一部分——在那里选择性模式与完整模式走的是同一条流水线——因此我们将其读作模型的非确定性。（融合 / mixture-of-agents 本身并非我们独有——OpenRouter 和其他工具里都有；这里的不同在于它被接进了智能体的循环、藏在那个成本感知路由器后面，并且是经过实测的，而不是一个你去挑选的模型。）
- **自己完成任务**（`chimera solve`）—— 它会规划、用工具行动，然后**验证并回退**：它会运行你设定的检查（例如测试），只有通过才保留改动，否则就撤销并重试。还可以选择在你项目的一份隔离副本上工作，在验证成功之前不碰任何东西。**而且，一段有说服力的文字并不算完成任务：**在没有 `--verify` 可依据时，一次没有改动任何磁盘内容的运行会被报告为失败，而不是成功——因为此时唯一还在评判它的，只剩一个读散文的模型，而它从来看不到 diff。每次尝试都会记录*是谁*批准了它（`verifier` / `diff+manager` / `diff` / `manager` / `none`），这样一张回执绝不会在不指明背后权威的情况下说“成功”。
- **专家团队**（`chimera crew`、`chimera crew-isolated`）—— 好几个各司其职的智能体分工完成同一件事。在隔离模式下，每个都在**自己的私有副本上并行**工作；安全的编辑会被合并，冲突之处会被标记出来而不是被悄悄覆盖，而且表现不佳的成员的改动可以被针对该成员的测试拒绝掉。一位主管可以把所有人的成果汇总成一份统一的报告。
- **委派与探索** —— 任何智能体都可以把一个自成一体的子任务交给一个全新的**子智能体**，后者只回报结果，让主上下文保持干净。**上下文探索器**（`chimera explore`）能在代码库里找到正确的文件和行，返回一个简短的答案，而不是把所有东西都一股脑倒出来。

### 🧬 记忆与自我改进
- **长期记忆** —— 它会保留短期、近期、事实性和关于你的记忆，还有一张记录事物之间如何关联的关系图。它可以把记忆存进一个快速的全文数据库、把你的偏好档案带进每一次对话、自动合并重复的笔记，并在你提到某个偏好时温和地建议把它保存下来。
- **学习新技能** —— 当它不止一次成功完成同一类任务时，会自动把它变成一个经过测试、可复用的技能。
- **一座你能读、也能扩充的精选技能库** —— [`skills/`](skills/) 里有 23 张技能卡，其中 13 张写自本项目自己踩过的坑。一张卡是**数据，不是代码**：一段 frontmatter 加上 Trigger / Do / Avoid / Check / Risk，它本身什么都不执行 —— 当某张卡匹配上时，智能体会把它读进 prompt，而这需要**用 `--skill-cards`（或 `CHIMERA_SKILL_CARDS=1`）手动开启，默认是关闭的**：那次本来要把“读卡”默认打开的登记 A/B，测出 +16.7 个百分点却*并不显著*，代价还是 +300% 的 token，所以它没能过自己那道翻转门槛，就一直关着（[`bench/skillcard/RESULTS.md`](bench/skillcard/RESULTS.md)）。这些卡按它们在工作流程里生效的位置分组（define · build · verify · review · ship），描述、正文和触发词都译成了九种语言 —— 由一个测试来保证诚实：只要某个语言的翻译过期了、或者只做了一半，它就会失败。用 `chimera skills-import skills/<name>` 导入一张。它同时也是门槛最低的贡献入口：审你的 pull request 就是读一页 markdown，而不是审一份 diff（[`skills/README.md`](skills/README.md)）。
- **可选的自我训练（进阶）** —— 它可以记录自己的经历，方便你日后据此微调一个模型。默认关闭；没有你的要求，什么都不会被拿去训练。

### 📏 一个可以被测量、并会说出自己迷路了的循环
一个智能体，是模型**加上它周围的一切**。周边这套机制决定了一次长运行是否还有用，而其中大部分在出问题之前
都是看不见的。Chimera 会测量自己的这一部分：

- **每次运行都留下一张回执。** `traces.jsonl` 里每次运行一行 JSONL：每一步的 token、调用了哪些工具以及返回了什么、历史在哪里被丢弃——还有**缓存命中率**，也就是提供方从缓存供给的 prompt token 占比。这才是循环真正的成本数字（一个缓存 token 的价格大约是新 token 的十分之一，所以 token 数完全相同的两次运行，价格可能相差约 10 倍），同时也是一个设计警报：只要有什么东西改写了 prompt 的开头，它就会塌下来，而这没有别的症状可查。不报告缓存信息的提供方会被记为**未知**，绝不会被算成未命中。
- **它会察觉自己已经不再前进。** 有两件不同的事都被叫作“上下文问题”：注意力在长 prompt 内部被稀释，以及一条*轨迹*悄悄停止积累、开始原地打转——单看每一步都没问题，整次运行却哪儿也没去。Chimera 的循环断路器抓的是紧凑版（12 次调用的窗口）；一次每二十轮才回头读同样三个文件的运行，会径直穿过它。所以还有第二个检测器，用来把**运行的前半段和后半段**做对比：重新推导出运行早已拥有的结果、失败率上升，或者在历史刚被丢弃之后冗余度骤增。它**只报告，不采取行动**——停止、重新规划、强制压缩都是合理的药方，而我们没有证据说明哪一种真的有用；此刻选定其中一种，恰恰会把这项工作想要消除的那种未经测量的假设固化进来。
- **长运行能挺过自己的上下文。** 窗口用尽过去会直接终结一次运行，这让窗口——而不是任务难度——成了真正的天花板。现在的压缩会保持系统消息原封不动（它是整个 prompt 缓存所锚定的稳定前缀），绝不让某个工具结果与它的调用失散，并且会**恢复运行继续做自己所需要的东西**：打开的文件、计划、任务清单、当前状态。它会明确说出自己丢掉了什么，而不是去做总结——智能体可以重新读一遍文件，却没办法“不再相信”一段编造出来的摘要。

### 🔌 连接与自动化
- **随处与它对话** —— 一个终端聊天、一个全屏终端应用，或者作为机器人跑在 **Discord、Telegram、Slack、Signal 和 WhatsApp** 上。还有一个简单的 HTTP 接口。
- **定时与主动性** —— 用大白话给它安排周期性任务（"每天早上，总结一下新闻"）。只要内置的调度器在运行，它就会**准时行动**，而不仅仅是在你给它发消息时。
- **工具与集成** —— 读写文件、运行 shell 命令、**读取完整渲染的网页并抓取或爬取整个网站**（带有防注入的结构化提取），并在沙箱中安全地运行代码。几乎可以接入任何 Web 服务（通过它的 API）或外部工具 —— 包括任何 **MCP 服务器**（[指南 + 可运行示例](docs/mcp.md)）—— 还能从你已经在用的其他智能体工具里导入你的配置。
- **开箱即用** —— 网页搜索、图像生成（托管**或完全本地**）、**语音转文字**和文字转语音、**媒体下载**、**数据分析与图表**、邮件、日历、代码执行等等，都已备好，随时可以开启。

### 🚀 随处运行，安全无忧
- **任意模型，一个界面** —— 托管模型或你自己的本地模型，如果某个模型宕机会自动切换（fallback），并在多个密钥之间轮换。
- **一条命令部署到服务器** —— 用 Docker（或裸机）运行它，让它持续在线并在重启后自动恢复。详见 **[docs/deploy.md](docs/deploy.md)**。
- **安全内核** —— 对每个操作进行一道检查（允许 / 警告 / 复核 / 拦截）、一个**需手动开启的**、网络隔离的容器用于运行不受信任代码（`CHIMERA_SANDBOX=docker`；默认的 local 运行器**并不**隔离），以及一份完整的行为审计日志。`review` 判定是停下来征询你、还是直接拒绝，由审批模式决定（`CHIMERA_APPROVAL_MODE=ask|deny|allow`）——无人值守时它选择拒绝，而不是替你捏造同意。
- **当它读到了不该信任的内容时，在落定之前先停下**（`--pause-on-taint`）—— 一次消费了不可信内容的运行会把自己停放起来而不是收尾，并等你决定。你可以接受这个结果、接受你自己改过的版本、给出指引让它再试一次，或者直接拒绝——在终端*或*桌面应用里都行。在你做出决定之前，什么都不会保存、什么都不会被学习；而暂停从不被报告为失败：它还没得出结论，它在等一个人。
- **一个能驾驶运行、而不只是启动运行的桌面应用** —— 五个去处，而不是十五项的菜单，支持十种语言。启动一次运行然后走开：回来时进度还在，状态栏在任何界面都会说明智能体正在做什么，停止按钮在每个界面都有效。Windows / macOS / Linux 的原生安装包见 [Releases](https://github.com/brcampidelli/chimera-agent/releases)。

## 快速开始

你需要 **Python 3.11–3.13**（[python.org](https://www.python.org/downloads/) —— 用 `python --version`
检查你装的是哪一版）；如果是从源码检出，还需要 [uv](https://docs.astral.sh/uv/)（一个快速的 Python 安装器）。

**1. 安装** — 从 PyPI：
```bash
pip install chimera-agent
```
这样就能使用 `chimera` 命令。（下面的示例用 `uv run chimera` 是针对从源码检出的情况——用 pip 安装后，直接运行 `chimera …` 即可。）若要参与 Chimera 本身的开发，请克隆仓库：
```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev
```

**2. 添加一个 AI 提供方密钥。** 最简单的是一个 [OpenRouter](https://openrouter.ai) 密钥 —— 一个密钥就能
解锁 100+ 个模型。
```bash
cp .env.example .env
# 打开 .env 并设置，例如：  CHIMERA_OPENROUTER_KEYS=sk-or-...
```

**3. 检查一切是否就绪**
```bash
uv run chimera doctor
```

**4. 试一试**
```bash
uv run chimera chat                         # 来一场对话（它会记住）
uv run chimera run "Explain what you can do in 3 bullets"
uv run chimera fuse "What's the best way to learn to cook?" --show-panel   # 看看多个模型融合的效果
uv run chimera solve "add a hello() function to app.py and a test for it" --verify "pytest -q"
```

**在服务器上运行它（让它 24/7 工作）：**
```bash
docker compose up -d      # 网关 + 调度器；自动重启
```
完整指南（Docker 或 systemd、定时任务、备份、安全）：**[docs/deploy.md](docs/deploy.md)**。

**5. 5 分钟内做一件真实的事：邮件分拣。** 把 Chimera 指向你的收件箱，得到一份
十秒钟的摘要 —— 只读，分类为 URGENT / PERSONAL / NEWSLETTER / COLD-SALES，
还可以选择每天早上定时运行：
```bash
uv run chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```
配置 + 每日定时 + 诚实的注意事项：**[examples/email_triage/README.md](examples/email_triage/README.md)**。

## 🧰 Chimera 能做什么 —— 以及如何开启每项功能

刚上手？装好 `pip install chimera-agent` 加一个 AI 密钥后，Chimera 就能用了。一些能力（读文档、听音频、
画图表、下载视频……）需要一个可选的小包 —— 叫做 **“extra（附加项）”** —— 有些还需要一个服务密钥。本节
列出**每一项能力、具体要装什么、以及试用的命令**。无需任何基础。

### 一次性全部开启
```bash
pip install 'chimera-agent[full]'     # 下面所有非 GPU 功能，一条命令
```
音频和视频还需要你电脑上装有 **ffmpeg**：
`macOS：brew install ffmpeg` · `Ubuntu/Debian：sudo apt install ffmpeg` · `Windows：choco install ffmpeg`。
想要精简安装？保留 `pip install chimera-agent`，只加你需要的 extra（见“需要”列）。**用 Docker？官方镜像
已内置以下全部功能。**

### 每项能力，逐条说明
**需要** = 要额外加什么：`—` 基础安装即可 · `[extra]` = `pip install 'chimera-agent[extra]'` · `密钥：X` = 在 `.env` 里设置的服务商密钥。

| 你能得到 | 需要 | 如何使用 |
|---|---|---|
| **记住你的聊天** | — | `chimera chat` |
| **问一个问题** | — | `chimera run "用 3 点解释 X"` |
| **全屏终端应用** | — | `chimera tui` |
| **桌面应用**（代码 · 编辑器 · 工作 · 知识 · 自动化，十种语言） | `[desktop]` 或直接下载 | `chimera app`，或从 [Releases](https://github.com/brcampidelli/chimera-agent/releases) 下载原生安装包（`.exe`/`.dmg`/`.AppImage`/`.deb`） |
| **做一个任务，只有通过检查才保留** | — | `chimera solve "给 app.py 加 hello() 和一个测试" --verify "pytest -q"` |
| **在落定任何从网上读到的内容前先问我** | — | 给 `chimera solve` 加上 `--pause-on-taint` |
| **逐步查看一次运行到底花了多少** | — | 已自动写好：`.chimera/traces.jsonl`（或 `$CHIMERA_HOME`） |
| **把多个模型融合成一个答案** | — | `chimera fuse "你的问题" --show-panel` |
| **一支专家智能体团队** | — | `chimera crew "你的任务" --mode supervisor` |
| **把整个项目做到完成**（危险步骤前会暂停征询你） | — | `chimera project start spec.yaml -w .` |
| **看图片**（视觉） | 密钥：Gemini 或 OpenAI | `chimera run --image photo.jpg "这是什么？" --model gemini/gemini-2.0-flash` |
| **听音频**（语音 → 文字） | `[stt]` + ffmpeg | `chimera agent "转写 meeting.mp3"` |
| **说话**（文字 → 语音） | 密钥：ElevenLabs 或 OpenAI | 让任意任务“把这段读出来存到 speech.mp3” |
| **读文档**（PDF、Word、Excel → 文字） | `[documents]` | `chimera agent "总结 report.pdf"` |
| **下载视频/音频**（YouTube 等 1000+ 网站） | `[media-dl]` + ffmpeg | `chimera agent "下载 <url> 的音频"` |
| **分析数据并画图** | `[data,viz]` | `chimera agent "加载 sales.csv 并画月度收入图"` |
| **网络搜索** | 密钥：Tavily | `chimera agent "上网搜：最新的 Python 版本"` |
| **读取并抓取真实网页**（真实浏览器） | — | `chimera agent "打开 example.com 并告诉我标题"` |
| **长期记忆** | — | `chimera memory add "..."` · `chimera memory search "..."` |
| **自动学会可复用技能** | — | 在 `chimera solve` 过程中发生；用 `chimera skills-stats` 查看（`chimera skills` 列出内置的） |
| **使用一张精选技能卡**（共 23 张，9 种语言） | — | `chimera skills-import skills/verify-before-claiming` |
| **安排周期性工作** | — | `chimera cron add brief "0 8 * * *" "总结新闻"` |
| **作为聊天机器人运行**（Discord/Telegram/Slack/Signal/WhatsApp） | `[messaging]` | `chimera serve --cron --discord` |
| **接入任意外部工具**（MCP） | `[mcp]` | 指南：[docs/mcp.md](docs/mcp.md) |
| **生成图片**（云端） | 密钥：OpenAI | 让任务“生成一张……的图片” |
| **生成图片**（完全本地，需要 GPU） | `[imagegen-local]` | 同上，离线 |

> 想精简就单独安装 extra —— `messaging`、`mcp`、`documents`、`media-dl`、`stt`、`data`、`viz`、`youtube`
> （都已包含在 `full` 里），以及仅限 GPU 的 `imagegen-local` 和 `train`。例如：`pip install 'chimera-agent[documents,stt]'`。

第一次来？上面[快速开始](#快速开始)里的四步就是全部的配置 —— 安装、一个密钥、`chimera doctor`、
`chimera chat` —— 从这里开始，上表里的任何命令都能直接用。完整命令参考与可复制粘贴的示例：**[docs/usage.md](docs/usage.md)**。

## 工作原理

给 Chimera 一个任务；它会规划（浮现出最相关的内置技能）、思考（遇到难题时融合多个模型）、用工具行动 ——
读取并抓取网页、编辑文件、制作图表 —— **检查自己的成果并只保留通过的部分**，然后从结果中学习 —— 把记忆和新技能回灌到下一个任务里。

```mermaid
flowchart TD
    U([你：一个任务或一个问题]) --> P[理解并规划]
    P --> Q{这是个难题吗？}
    Q -- 是 --> FUSION[询问多个模型<br/>· 一位评审比较它们<br/>· 一个综合器写出最佳答案]
    Q -- 否 --> ONE[用一个快速模型]
    FUSION --> ACT[行动：使用工具、编辑文件、<br/>读取并抓取网页、制作图表，<br/>或委派给子智能体]
    ONE --> ACT
    ACT --> V{成功了吗？<br/>运行测试 / 检查}
    V -- 是 --> KEEP[保留改动]
    V -- 否 --> REVERT[撤销并带着教训重试]
    REVERT --> ACT
    KEEP --> LEARN[学习：把重要的东西存入记忆，<br/>把重复的工作变成可复用的技能]
    LEARN --> U
    MEM[(长期记忆)] -. 回忆 .-> P
    LEARN -. 写入 .-> MEM
    SKILLS[(技能库)] -. 浮现相关技能 .-> P
    GOV[[对每个操作的安全检查]] -. 守护 .-> ACT
```

## 命令

每条命令都是 `chimera <name>`（安装之前是 `uv run chimera <name>`）。

```bash
chimera doctor / models / features    # 检查配置、列出模型、查看可选能力
chimera chat                          # 跨轮次记忆的交互式助手
chimera tui                           # 全屏终端应用
chimera run "PROMPT" --image pic.png  # 单次问答（可以读取一张图片）
chimera fuse "PROMPT" --show-panel    # 融合多个模型：专家组 -> 评审 -> 综合器
chimera solve "TASK" --verify "pytest -q" --isolate   # 完成一个任务；只有检查通过才保留改动
chimera crew "TASK" --mode supervisor         # 一个专家团队攻克一个任务
chimera crew-isolated "TASK" -W "name:role" --verify "..." --synthesize   # 团队，每个都在自己的隔离副本中
chimera explore "where is login handled?"     # 找到正确的文件/行，得到一个简短的答案
chimera deliver "a launch plan" -o plan.md    # 产出一份精致的文档
chimera serve --cron [--discord|--telegram|--slack|--signal]   # 作为服务运行：聊天机器人 + 调度器
chimera cron add "brief" "0 8 * * *" "Summarize the news"       # 安排周期性工作
chimera memory add / graph / consolidate      # 长期记忆：保存、关联、整理
chimera kanban add/board/run                   # 一个把工作分派给智能体的任务板
chimera workflow flow.yaml                     # 运行一个用文件描述的可重复自动化流程
chimera orchestrate "TASK" --dry-run           # 拆分给多个限定范围的 worker；--dry-run 不花一分钱
chimera project start spec.yaml -w .           # 把整个项目做到完成，危险步骤前会先征询你
chimera skills-import skills/<name>            # 加载一张精选技能卡（是数据，不是代码）
chimera skills-stats / skills-pending          # 学到的技能：使用情况、胜率、还有哪些等着审阅
chimera migrate <source> <dir> --apply         # 从另一个智能体工具导入设置、技能和记忆
chimera evolve status / tune / recipe          # 可选：自我优化；准备数据以微调一个模型
chimera fusion-bench / skillcard-bench / schema-bench / sandbox-bench   # 诚实的 A/B 基准测试：在信任某项功能之前，先测量它的成本、质量与副作用
chimera pet new --name Chimi                   # 领养一个小小的虚拟伙伴 :)
```

查看 **[使用指南](docs/usage.md)**，了解每条命令及可复制粘贴的示例。

## 架构

Chimera 是一个 Python 包，各个部分界限分明，因此你可以单独理解或扩展其中任何一块：

```
chimera/
  core/          智能体循环：规划、行动、验证、保留或撤销，以及隔离的工作副本
  fusion/        "众多智慧"引擎：专家组 -> 评审 -> 综合器 + 智能路由器
  memory/        短期 / 近期 / 事实性 / 关于你 的记忆 + 一张关系图
  skills/        内置技能库，以及如何找到相关的技能
  evolution/     从成功中学习新技能，以及它据以学习的经验
  governance/    安全内核（允许/警告/复核/拦截）、审计日志和变更控制
  orchestration/ 智能体团队：角色、crew、隔离的并行 worker、统一报告
  ecosystem/     进阶自我改进：设计智能体的智能体、可选的模型训练
  kanban/        一个把卡片交给智能体的任务板
  workflow/      用一个简单的文件描述一个可重复的自动化流程并运行它
  eval/          诚实基准的测试框架：SWE-bench、Terminal-Bench、注入红队
  tools/         内置工具（文件、shell、网页、搜索）+ 代码执行
  scrape/        完整渲染的网页读取、抓取与整站爬取
  rag/           对一个仓库做语义检索 —— 回答那种没有确切字符串可搜的问题
  sandbox/       在本地或一个封闭隔离的容器内运行工具
  integrations/  连接外部工具和任意 Web API
  scheduler/     周期性任务 + 准时触发它们的守护进程
  migration/     从其他智能体工具迁移你的配置
  providers/     通往每个模型的统一接口，带 fallback 和密钥轮换
  interface/     共享的对话引擎（供聊天、应用和机器人使用）
  server/        消息网关和 HTTP 接口
  api/           桌面应用所对话的 HTTP+SSE 接口
  acp/           Agent Client Protocol，两头都走：既能驱动别人的编码智能体，也能被编辑器驱动
  lsp/           来自真正语言服务器的诊断，让编辑器和 CI 说同一套话
  complete/      行内补全 —— 光标前方的那段灰色文字
  proc/          长期存活的子进程：生命周期、消息分帧、进程监管
  tui/           全屏终端应用
  cli/           `chimera` 命令
```

完整设计请见 [docs/architecture.md](docs/architecture.md)。

## 愿景与目标

**Chimera 的目标很简单：一个人人都能运行的 AI 智能体，它通过结合多个模型而不是信任单一模型来更好地推理，它真的
会用得越多越好用，并且一路上始终保持安全和完全开放。**

如今大多数 AI 工具，要么聪明却健忘（聊天一结束就丢掉一切），要么强大却封闭（你无法掌控它们）。而且很多试图
"自我改进"的工具，在长期运行中会悄悄变得*更糟*。Chimera 是我们对一条不同道路的尝试：

- **更好的思考，而不是更高的账单** —— 只在有帮助时才结合多个模型，让质量提升而不浪费。
- **真正的记忆和真正的技能** —— 记住重要的东西，把重复的工作变成可复用的能力。
- **能持续的进步** —— 通过检查自己的成果，并把状态安全地保存在模型之外，来抵抗那种拖垮其他智能体的缓慢退化。
- **安全且透明** —— 每个操作都可核查，破坏性的操作会先询问。
- **对所有人开放** —— 免费、采用 Apache-2.0 许可、由社区驱动、无锁定。

它还处于早期（alpha），我们很看重坦诚：它尚未在高强度生产使用中得到检验。如果这个愿景让你心动，我们非常
欢迎你来帮忙一起实现它。

## 开发

```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev

uv run ruff check .      # 风格/代码检查
uv run mypy chimera      # 严格类型检查
uv run pytest -q         # 测试套件
```

非常欢迎各种贡献 —— 代码、文档、点子、缺陷报告。从 [CONTRIBUTING.md](CONTRIBUTING.md) 和我们的
[行为准则](CODE_OF_CONDUCT.md) 开始吧。想教 Chimera 一些新东西？**[扩展指南](docs/extending.md)** 会带你用 Python 添加自己的
**工具、技能或配方**（附可直接复制的示例）。而门槛最低的一种贡献是一张**技能卡** —— [`skills/`](skills/) 里
单独的一个 markdown 文件，不用写 Python，也不用先开 issue。发现了安全问题？请看 [SECURITY.md](SECURITY.md)。

## 社区

有问题、有点子，或者想做贡献？**[来 Discord 加入我们](https://discord.gg/ACvBbrmguV)** —— 欢迎每一个人。

更喜欢 Reddit？在 **[r/ChimeraAgent](https://www.reddit.com/r/ChimeraAgent/)** 关注更新与讨论。

## 支持项目

Chimera 完全免费、开源，公开开发。如果它对你有帮助，欢迎通过一次性捐赠来资助
它的开发 —— 每一份支持都很重要，我们万分感激。💜

**[💜 通过 Stripe 捐赠](https://buy.stripe.com/9B6aEQ57q91m1Gp7Lz77O01)**

## 许可证

[Apache-2.0](LICENSE) —— 可自由使用、修改和在其之上构建。
