---
source_sha256: 39b9206b1943aee5c2c508bd1f97b3c9bb931d3a4d75eb5dc24f861497cdfe04
---

# 融合存证 —— "带存证的选择性融合"

Chimera 的推理核心会混合一个模型**面板**（panel → judge → synthesizer，面板 → 评审者 → 综合
器）。融合能换来更高的质量，但也要消耗更多 token，所以真正诚实的问题从来不是"融合好不好？"，
而是"**在这里，它值不值得？**"。存证正是用数字而非口号来回答这个问题。

每一次融合运行都可以被计价成一份**存证**：每一位顾问（advisor，面板成员）、评审者、综合器各花
了多少——都按*各自*所用模型的价格计算——以及选择性模式是否让面板提前短路跳过。把这些存证持久
化保存下来，你就得到了一条可公开发布的**成本 × 质量曲线**。

## 试一试

```bash
# Show the itemized per-advisor cost of one run:
chimera fuse "Explain CAP theorem simply" --show-cost

# Append each run's receipt to a JSONL, then summarize the curve:
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` 会报告**融合率**（完整面板真正运行的比例，相对于选择性短路的比例）、在已知
价格的运行中的平均/总成本，以及——当存证中带有通过/失败的质量信号时——通过率以及**每个通过
答案所花的美元数**。

## 诚实规则（从设计上就保证）

- **token 数是实测的；美元金额是估算的。** token 数量来自 provider；美元金额则按大致的公开
  **标价**计算，因此一份存证只是一个估算器，而不是一张账单。
- **未知模型 → 未知成本，绝不记为零。** 如果任何一个阶段用了一个没有登记价格的模型，该份存证
  的总额就是 `None`（未知），这样一个缺失的价格就无法伪装成"免费"。价格可以在代码中被覆盖设置
  （`chimera.fusion.set_price`）。
- **按顾问归属成本。** 面板的成本会*按模型*拆分列出（`receipt.advisor_costs`），因此你可以看清
  哪位顾问真正物有所值——这正是选择性融合背后的实质内容，而不只是一句口号。

## 为什么要有这个功能

这个领域正在转向路由/级联式方案（只有当赌注够大时才多花钱），而不再是持续常开的融合。存证正是
让 Chimera 得以**做到选择性融合、并证明这样做确实值得**的东西——成本 × 质量曲线就是证据，连
融合*没有*带来帮助的那些运行也会一并公开发布。
