---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# 基准测试 —— 证明"弱模型也能打出高分"

Chimera 的核心论点是：结构（scaffolding）能让一个**弱/廉价**模型发挥出超出其本身水平的实力。要
诚实地证明这一点，唯一的办法是在标准基准上做受控 A/B 实验：固定任务子集与模型，让脚手架成为
**唯一**变量，并给出带置信区间的差值——而不是一句空洞的"变好了"。（独立研究发现，仅凭脚手架的
不同，同一模型的分数就能摆动约 7 个百分点，因此一个未经限定条件的分数说明不了*你*到底贡献了
什么。）

## 实验设计

**基准：** [Terminal-Bench 2.0](https://www.tbench.ai/) —— Docker 任务 + 指令 + 验证测试，由这些
测试判定通过/失败，并由与 agent 无关的 **Harbor** 脚手架驱动执行。

- **A 组（基线）：** 在 Harbor 中立脚手架下运行的一个免费模型——"仅靠弱模型自己"。
- **B 组（处理组）：** **同一个**模型、**同一批**任务 ID，由 Chimera 驱动。
- **指标：** pass@1。**核心数字：** Δ = rate(B) − rate(A)，附 95% 置信区间。
- **诚实性保障：** 固定任务 ID 子集（并公开发布）、至少跑 3 个随机种子（seed）、公开全部会话
  记录，并只把前沿模型的结果作为*上限参考*列出——而绝不作为对比对象。

### 结果 —— 而它对我们不利

本页原本以那个*将会证明*论点的数字收尾："免费模型单独跑 = X%，免费模型 + Chimera = Y%，Y ≫ X"。
该实验此后已经跑过，而 Y 落在了 X **之下**。在两个分支使用同一模型的 N=40 预注册切片上
（`deepseek-chat-v3.1`）：**7.5% → 2.5%**，**配对 Δ −5.0 个百分点，95% 置信区间 [−5.0%, +1.6%]
—— 不显著**。脚手架没有抬升一个本就称职的模型；两个分支都停在被方差主导的地板上。完整报告（含在
运行前写下的预注册）：[`bench/terminal_bench/RESULTS.md`](../../../bench/terminal_bench/RESULTS.md)。

那句承诺 `Y ≫ X` 的话，活得比反驳它的那次运行还久 —— 在本页和九种翻译里都是。它被记录在这里而不是
被悄悄删掉，因为一个唯一真实资产就是诚实测量的项目，负担不起一个预言了与自身结果相反内容的页面。

## 如何运行

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimera 通过 `chimera/eval/terminal_bench.py` 接入，作为处理组的 agent（`make_chimera_tb_agent
(model)` 会构建一个 Harbor 的 `BaseAgent`，用带脚手架标志的 `chimera solve` 来运行）。让 Harbor
对每一组分别指向同一份固定任务子集和同一个免费模型；具体的 `harbor run` 调用方式和
`--agent-import-path` 请参阅 [Harbor 文档](https://www.tbench.ai/)。

## SWE-bench Verified（第二个记分板）—— **跑了四次**

Terminal-Bench 在 CLI 任务上证明了这一论点；SWE-bench 则在真实的 GitHub 缺陷修复任务上证明它——
给定某个基准提交（base commit）下的仓库和一个 issue，agent 必须产出一个补丁，使该实例的
`FAIL_TO_PASS` 测试转为通过，同时保持 `PASS_TO_PASS` 测试全绿。"Verified" 是经过人工核验的子集。

### 结果

在同一份冻结的、来自 `django/django` 的 19 个实例切片（难度最低的一档）上，用
`deepseek-chat-v3.1`、pass@1 指标，跑了四次预先登记（pre-registered）的实验，全部**仅**由官方
`swebench` 4.1.0 评测工具在 Docker 中评分。完整报告见：
[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)。

| 实验轮次 | 基线 | + Chimera | 配对 Δ | 95% 置信区间 | |
|---|---|---|---|---|---|
| 1（`max_steps=8`） | 36.8% (7/19) | 36.8% (7/19) | +0.0% | [−8.5%, +8.5%] | 不显著 |
| 2（`max_steps=30`） | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] | 不显著 |
| **3（复现）** | 34.1% (14/41) | **43.9% (18/41)** | **+9.8%** | [−3.5%, +16.7%] | 不显著 |
| **合并（次要）** | 36.7% (22/60) | 48.3% (29/60) | **+11.7%** | **[+0.8%, +16.4%]** | **显著** |
| 4（归因） | 34.1% | *仅脚手架* 39.0% | +4.9% | [−7.6%, +14.2%] | 不显著 |

第 1 轮是一个**确切的零值**，原样发布，未做任何修饰。第 2 轮修复了两个属于*我们自己*的问题——
脚手架当时没有用上它最强的机制，而且 8 个工具调用步数不足以在一个 250 MB 的仓库里完成导航——
修复之后战绩是**赢 3 个实例、输 0 个**。这一对结果才是真正的发现：当 agent 的步数预算被卡死时，
脚手架的价值*为零*；不被卡死时，它的价值是*三个实例*——而且它是靠编辑得**更准**（编辑时精确率
69% 对 57%）赢下来的，而不是靠编辑得更多。

> ⚠️ **这些数字都不是 SWE-bench Verified 分数。** 这份切片被刻意选得容易、且只取自单一仓库，
> 目的是让配对 A/B 有足够的空间去测量；真正的 Verified 分数需要跑完整的 500 个实例。而且这个
> 差值**并不显著**——在 8 对"双输"配对之下，n=19 只留下三对有信息量的样本。

第 2 轮还发布了一次**更正**：我们此前为第 1 轮空补丁问题追溯出的机制其实是错的（真正的修复点
是步数预算，而不是我们当初归咎的 diff-gate），我们用同样醒目的方式对此进行了更正。

那个在三个有效配对上的 3–0，正是幸运样本会产生的形状，而预先登记给它的判断是**有三分之一的可能就只是运气**。于是第 3 次运行在**我们从未见过其结果的 41 个实例**上重复了一遍，其他什么都没改。效果**再次出现**：+9.8%，落在登记的 +5 到 +20 区间内，而且这一批切片比第 2 次的*更难*（基线 34.1% 对 42.1%）。第 4 次运行随后在同样的 41 个实例上把脚手架与 diff-gate 拆开：**各 +4.9%**，机制是精确率，它从 50% → 59% → 67% 一路上升，而打补丁的比例始终没动。单独看没有一次是显著的；合并的 n=60 是显著的——而它被预先登记为**次要**，正是因为它把见过的与没见过的数据混在了一起。

### 适配器

该适配器（`chimera.eval.swe_bench`）对自己的边界很坦诚：纯粹的部分——按实例调用的 `chimera
solve`（处理组）以及对官方评测报告的解析——都在这里实现并有单元测试；而数据集本身和 Docker
评测工具是**可选安装、不随包分发**的，通过/失败的判定始终来自 SWE-bench 自己的测试，绝不自我
汇报。

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

两份报告都会被投影到共同的实例 ID 列表上（缺失的 id 计为未解决），因此两组始终是在完全相同的
实例集合上进行比较——之后套用同样的 Newcombe 置信区间判定方法。

## 给 A/B 打分（不需要跑基准）

一旦每一组都产出了逐任务的通过/失败结果，统计工作只需一条命令——这一步**不需要任何额外安装**，
因此诚实报告引擎始终可用：

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

每份文件都是一份针对**同一批**任务 ID 的布尔值 JSON 列表（或 `{task_id: bool}` 形式）。输出内容
包括：每一组经 Wilson 区间修正的通过率、两者的差值、对应的 Newcombe 95% 置信区间，以及这个差异
是否**显著**（置信区间不包含零）。如果不显著，也会如实报告——要么扩大子集/增加随机种子，要么
说明这项特性确实没有让分数发生变化。

同一个 `bench-compare` 是衡量此后每一项特性的尺子：M14 阶段的每一项新增功能，都必须在完全相同
的子集上证明它能移动 Δ 值，否则就会被砍掉。

## 诚实性陷阱（应当避免的做法）

- **数据污染** —— 公开的 SWE-bench 已被记录存在解答泄漏问题；应优先选用抗污染的数据集，并在
  报告中注明这一警示。
- **脚手架混淆** —— 绝不报告一个孤立的"我们跑出了 X%"；只有 A/B 的差值才能剥离出 Chimera 自身
  的贡献。
- **基线选错 / 挑数据** —— 应将"弱模型 + Chimera"与*同一个弱模型单独运行*进行比较，在*完全
  相同*的任务 ID 上，附带随机种子和完整日志。前沿模型只是一个上限参考，而不是竞争对手。
