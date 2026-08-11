---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
---

# 安全与防护措施

Chimera 能执行 shell 命令、编辑文件、调用 API，还能修改自己的技能。它内置了**纵深防御**，而且
——这一点很重要——文档会明确说明每一层防御在哪里*止步*。

!!! warning "唯一的一条规则"
    当你赋予它自主权时，以上这些防护措施都无法替代**在一个隔离环境中运行它**这件事。默认的
    `local` 运行方式并不是隔离的；处理不可信的工作时，请使用 `CHIMERA_SANDBOX=docker`
    （关闭网络，可选地跑在 gVisor 之下）。

## 各道防线

- **治理内核** —— 每一次受管控的工具调用都会被判定为允许 / 警告 / 复核 / 阻止。它是对危险
  shell 特征的一道廉价初筛，而不是安全边界本身。
- **沙箱** —— 一个用后即弃、断网的容器（`CHIMERA_SANDBOX=docker`），可以用 gVisor
  （`CHIMERA_SANDBOX_RUNTIME=runsc`）进一步加固。
- **按会话的工具白名单** —— 只给某次运行授予它需要的工具；其余工具会被彻底从模型的 schema 中
  移除。
- **污点追踪**（`--taint`） —— 不可信内容会被围栏为数据，其溯源信息会一路跟随进入记忆和技能
  （来自一次带污点运行的技能会被暂扣以待复核），而且一旦某次运行被标记为带污点，危险工具的
  可用范围就会收紧。
- **隔离读取器** —— dual-LLM / CaMeL 模式：不可信内容由一个没有工具权限的模型来读取，它只能
  输出经过 schema 校验的字段，因此一次注入攻击无法产生新的指令或工具调用。
- **跨 agent 监控器** —— 在扇出（fan-out）场景下，按 worker 划分的监控器看不到*拆分式*的攻击
  流（一个 worker 抓取不可信内容，另一个不同的 worker 把它当作汇点——抓取和汇点分别记录在不同
  的台账里）。一个聚合监控器能看到整个扇出过程；它在 `solve-batch` / `crew-isolated` 场景下
  **始终开启**。

## 扇出场景：跨 agent 监控器

当多个使用工具的 worker 并行运行时（`solve-batch`、`crew-isolated`），每个 worker 都会拥有
自己的能力台账，批次结束后会有一个聚合监控器对所有台账进行检查。它能捕捉到任何单个 worker
监控器都看不到的模式——比如拆分式外泄：worker A 抓取不可信内容，worker B 执行或外泄它：

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

它永远只会**升级为复核**——绝不会阻断一次运行——而且纯粹只做可观测性记录（只记录变化，不改变
任何行为）。在此基础上再加上 `--taint`，还能进一步启用每个 worker 的自适应白名单（这样"带污点
时视为危险"的工具就需要经过批准）。

## 经过测量，而非凭空断言

```bash
chimera redteam
```

会让一个注入语料库跑过整套系统。在内置语料库上，污点层把**攻击成功率从 100% 降到了约
14%**——而且报告会明确*指出*仍有哪些攻击能够得逞（通过一个被允许使用的工具进行外泄），而不是
声称自己做到了 100%。

## 对外暴露 HTTP 服务器

`chimera serve` 默认绑定在 `127.0.0.1`。它那些会改变状态的接口（`/chat`、`/a2a`、
`/webhook/*`）会驱动 agent 采取行动，因此**在把服务器暴露到网络之前**，请先设置一个 bearer
token：

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

设置好之后，这些 POST 接口在没有匹配的 `Authorization: Bearer` 请求头时会返回 `401`（`GET
/health` 和 A2A 的 agent-card 接口始终保持开放）。对于 WhatsApp 的入站 webhook，请把
`CHIMERA_WHATSAPP_APP_SECRET` 设置为你的 Meta 应用密钥——之后 Chimera 会校验每个请求的
`X-Hub-Signature-256` HMAC 签名，并对伪造的请求体返回 `403` 拒绝。这两项都是可选启用的（不设置
= 不做鉴权，在本机运行没问题）；一个面向公网的部署应当设置它们（或者放在一个带鉴权的代理
之后）。

## 诚实的边界

这里测量的是：一个*已经被注入*的 agent，其有害动作是否会被拦下——而不是模型本身一开始能不能
被注入。对不可信文本内容的自由形式推理，以及通过合法必需的工具进行外泄，仍然是尚未解决的开放
问题（见 [issue #5](https://github.com/brcampidelli/chimera-agent/issues/5) 跟踪记录）。

完整且始终保持最新的安全政策见
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md)，其中也包括
如何报告一个漏洞。
