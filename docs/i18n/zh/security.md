---
source_sha256: 0f6fea8c584991f0722cb5e5454502c825585f4066f672324c4ab3011ca109dd
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
  **验证命令**运行在与 agent 自己的 shell 相同的沙箱里；而当那个沙箱并不隔离时，一条不是你
  亲手敲下的命令——从仓库推断出来的，或是从一个定时任务、一张卡片或一个工作流里读到的——
  同样要走一遍 `CHIMERA_HOST_EXEC` 确认；一旦被拒绝，它选择弃权，而不是照样执行
  （`CHIMERA_VERIFY_NETWORK=1` 可以给 docker 里的验证器联网；在内核级沙箱里则做不到，所以
  需要联网的验证器只能跑在宿主机上，而且仅限你亲手敲下它的时候）。
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

同一条命令还会打印出**代价**，而这一页的第一个版本并没有打印它：在没有人可以询问的情况下，
收紧会拒绝掉**100% 那些先读取了任何外部内容的正当工作**——修好 issue 点名的那个文件，装上
文档描述的那次升级——于是登记在册的关卡（过度阻断 ≤ 5%）没有通过。这个数字不是调参的问题；
那道关卡后面本来就没有人。默认的审批模式是 `ask`，而在桌面端它现在真的会问了：一次被收紧的
工具调用会变成屏幕上的一个问题，并附上台账给出的理由，可以用一个按钮或者 `chimera approve`
来回答；沉默超过 `CHIMERA_APPROVAL_WAIT` 秒即视为拒绝。当由本人来批准他自己请求的那份工作
时，过度阻断是 0%，而攻击阻断率纹丝不动——按每一组分别测量，见
[`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md)。
通过被允许的工具进行的外泄也被同一处改动堵上了：一次带污点的运行里，带查询字符串的
`http_get` 会被判为复核；而为此加进语料库的那两条正当的带查询字符串 GET 请求，正说明了这样
做要付出的代价。

### 被投毒的记忆，跨越多次运行

`redteam` 测量的是一次运行。另一种形态更慢，而且塞不进一个进程里：运行 A 读到一个被投毒的
页面，并把自己"学到"的东西存了下来；几天之后，运行 B 问了一个毫不相干的问题，而记忆召回把
这条被植入的事实递给了模型。

```bash
chimera memory-poison
```

同样是离线且免费的。它会逐一消融夹在这两次运行之间的三道防线——`tainted` 溯源标记、记忆召回
的准入关卡，以及这条事实带进提示词里的 `[unverified]` 标签——因为如果只有一个数字，那么其中
任何一道防线其实什么都没做，都与这个数字相容。真正该登头条的是**没有被标记**就抵达的东西，
而不是被拦下的东西：一条带着自己来历的被投毒事实，是模型已经被提醒过的事实；而一条没有标签
的，则和 agent 自己核实过的东西无从分辨。

第一次运行得出的两个结果值得直说，因为这两个都不是在替我们说好话：

- **我们发布出去的这套配置没有通过它自己的关卡——输在代价上。** 它标记出了 100% 的投毒内容，
  代价是同时毁掉了 25% 的诚实记忆。被误伤的对象都点了名：一份为了解释某种攻击而引用了它的
  安全文档，以及一张转发了一次攻击尝试的支持工单。一个基于内容的模式匹配器，分不清一段引用
  和一条命令。
- **在这个语料库上，内容关卡并没有增加任何溯源标签尚未覆盖到的东西。** 它被测量到的全部效果，
  就是它拿掉的那些诚实记忆。十五条手工写下的条目是一个指向，而不是一个定论，正因为如此，我们
  并没有凭它删掉任何东西。

阈值、方法，以及这些数字*不*足以支持的结论，都写在
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md)
里，而它在第一次运行之前就已经定好。

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
