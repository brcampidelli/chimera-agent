---
source_sha256: fa20c6b6da0eaf4a321672404884fa172ead932c65c3fc0a2873bbf7dbff906e
---

# 安全与防护措施

Chimera 能执行 shell 命令、编辑文件、调用 API，还能修改自己的技能。它内置了**纵深防御**，而且
——这一点很重要——文档会明确说明每一层防御在哪里*止步*。

!!! warning "唯一的一条规则"
    当你赋予它自主权时，以上这些防护措施都无法替代**在一个隔离环境中运行它**这件事。默认的
    `local` 运行方式并不是隔离的；处理不可信的工作时，请使用 `CHIMERA_SANDBOX=docker`
    （关闭网络，可选地跑在 gVisor 之下）。

## 提示词和机器人文本中的代理语气

提示词和机器人模板不得让代理声称自己有感情、关心、友谊、关系或对自身的承诺。
说明它做了什么、没能做什么，以及有多大把握。经过校准的第一人称不确定表达
（例如“我不确定，但……”）是允许且有用的。

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

**批准，以及一个被拒绝的 worker 长什么样。** 每个 worker 都带着自己的批准者和自己「被允许做了
什么」的记录，所以一个被拒绝的任务会照实说出来，而不是报告 `ok`：

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

括号里的 `ok` 是循环自己的判定，两者不一致正是重点：一次被拒绝的调用会以一条普通的观察文本返回，
worker 把它当作任何工具结果来读，继续往下走，最后以散文收尾。能问谁由 `CHIMERA_APPROVAL_MODE`
决定——`deny` 当场拒绝，`ask` 在终端上提问，否则把问题写下来交给 `chimera approve`，并按每个问
题、每个 worker 等待 `CHIMERA_APPROVAL_WAIT` 秒。沉默永远是拒绝。

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

### 与第二个人共享一段对话

桌面应用可以通过一个**只打开那一段对话的令牌**来共享编码对话——绝不是服务器令牌。所有者为每位访客签发一个
（`POST /api/code/sessions/{id}/share`），并可以单独撤销；删除对话会撤销全部。访客能到达 `/guest/api/…` 下的四条路由：
读取对话、实时跟随、向其中发送消息、查看谁在场。共享令牌打不开其他任何东西，访客应用也没有回答治理卡片的路由——那些留在所有者的屏幕上。

当所有者打开网络之门（`POST /api/code/share/network`，用 `DELETE` 再关上，启动时从不打开）时，访客应用也是**唯一**被提供的东西：
局域网里的机器只能到达这四条路由，到达不了服务器令牌保护的任何东西。交出链接前请直说：访客可以让代理做所有者能让它做的任何事，
在所有者的项目里，用所有者的工具和花费——在请求的默认约束下，且不能批准卡片。

### 你自己的手机或浏览器：配对设备

访客链接是给别人的。所有者在自己的手机上使用的是配对：`chimera pair`（或 设置 › 访问入口 里的**配对设备**）会打印一个**只能用一次、五分钟内有效**的验证码，并附上配对地址的二维码。
验证码要输入到第二个局域网监听器提供的页面里——它在每次启动时都是关闭的，只由所有者打开（`chimera pair --lan`，或卡片上的**打开**）——
输入后变成一个 30 天的 cookie（`HttpOnly`、`SameSite=Strict`，经 https 时加 `Secure`），用从所有者的设备密钥（`<home>/devices.key`）派生出的密钥签名。
输错五次验证码会作废所有仍有效的验证码；同一地址错五次，该地址会被拒之门外十五分钟。

已配对的设备**在阅读和对话上**就是所有者：对话、轮次、指引、对代理自身提问的回答，以及拒绝一次批准。它不是所有者的屏幕。批准仍然需要每个问题的验证码（研究 30，S30-30），
而那些分发访问权限、修改设置、授权文件夹、启动机器人或停止服务的路由，对设备一律返回 `403`。访问入口卡片列出每台设备及其最近使用时间和网络（`192.168.1.x`）；
**撤销**会立即终止其中一台，包括它正开着的实时流，而**撤销所有设备**会更换密钥，因此在此之前签发的任何 cookie 都无法通过校验。桌面桥接不能配对、签发验证码、列出或撤销设备。

出门在外时，Chimera **不运行任何自有的中继**。如果你想从外部连到设备监听器，就在它前面放一个你自己控制的隧道——两台设备上都装 [Tailscale](https://tailscale.com/)，
或者一个通往 `http://127.0.0.1:<port>` 的 [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)——并且要有意识地做这个决定：它会把同一扇门延伸到隧道能到达的任何地方。
绝不要为此把应用本身绑定到 `0.0.0.0`。

## 桌面应用的后端

应用的后端监听在 loopback 上，而 loopback 不是一个人：这台机器上的任何进程都能连到那个端口。

- **每条路由都要令牌。** 桌面应用总是带着一个 bearer 令牌启动后端——要么是每次启动新生成的密钥，要么是所有者已经设置的 `CHIMERA_SERVER_TOKEN`。
  此后每条 `/api/…` 路由没有它都返回 `401`，`/api/health` 也不例外。只有应用的静态文件和访客应用（`/guest/api/…`，它自己校验共享令牌）保持开放；
  MCP 桥接（`/api/bridge/…`）保留它自己的令牌。外壳通过环境变量把每次启动的令牌交给后端，通过 webview 的初始化脚本交给窗口——从不放进命令行、URL 或文件。
  后端启动的进程不会继承它：沙箱子进程的环境，以及 `gh` 和 `git` 运行时的环境，都会去掉所有名字像密钥的变量（`GH_TOKEN` 等 gh 自己的凭据除外）。
- **不带验证码的批准需要屏幕。** 令牌说明的是谁可以调用 API，而不是所有者正在看着。不带验证码的"同意"（`POST /api/approvals/{id}`）只有在请求除 bearer 令牌之外，
  还在 `X-Chimera-Screen` 中带上屏幕凭据时才会成功。它在每次启动时生成，只保存在内存中：从不进入文件、记录、日志、`.env` 或任何路由的响应，绝不等于 bearer 令牌，
  并在被读取时从环境中移除（外壳以 `CHIMERA_SCREEN_TOKEN` 传入）。任何从磁盘读到的令牌——`service.token`、`.env` 里的 `CHIMERA_SERVER_TOKEN`——要说"同意"仍需每个问题的验证码；
  拒绝不需要验证码。`chimera service` 不持有屏幕凭据，所以在那里每个"同意"都需要验证码。在浏览器中使用的 `chimera app` 会把它放进返回给 loopback 请求的页面
  （`<meta name="chimera-screen">`），位置和条件与该页面已经携带 bearer 令牌时完全相同——暴露程度一样，这是所有者 2026-10-16 的决定。
- **后台服务。** `chimera service start` 在没有窗口的情况下运行同一个后端。它发布 `~/.chimera/service-<channel>.json`（0600，原子写入；`CHIMERA_SERVICE_DIR` 可改变位置），
  其中带有所有者的持久令牌（`<home>/service.token`，或所有者选定的 `CHIMERA_SERVER_TOKEN`），客户端只有在 `GET /api/info` 返回相同的 pid、版本、id 和数据文件夹之后才信任这个文件。
  这个令牌能打开整个 API，所以与桥接的令牌分开：MCP 一侧没有任何东西读取服务的文件，桥接也拒绝把它们当作文件处理。已安装应用的启动记录 `~/.chimera/desktop-install.json`
  只含四个路径变量——`CHIMERA_HOME`、`CHIMERA_WORKSPACE`、`CHIMERA_SHELL_PREFS`、`CHIMERA_SHELL_STATE`——写入方拒绝任何其他名字，`start` 在依据它运行任何东西之前都会先校验。

### 通过桥接的完全控制

桌面桥接（`chimera mcp desktop`）有三个开关，每一个在前一个关闭时都不起作用：**允许 Claude 操作此应用**、**完全控制**，以及自所有者 2026-10-18 的决定起位于 设置 > 安全 的 **通过桥接的完全控制**。

- **只有界面能开启。** `CHIMERA_BRIDGE_FULL_CONTROL` 不在可编辑设置列表中，因此 `PATCH /api/config` 对任何调用者都拒绝它，桥接的 `settings.edit` 也按名称拒绝它。它唯一的路由 `POST /api/security/bridge-full-control` 拒绝任何经桥接转发的请求，并且只有携带界面凭据时才会开启；仅有所有者的令牌只是磁盘上可读的 `service.token`。界面会先要求确认。关闭不需要界面：它只会收窄。
- **它增加的内容。** 所有者的决定（允许在某文件夹中运行命令、保存带有工具授权的代理、已记住的审批规则、全部拒绝、记住某个项目、回答设置建议、插件和市场、清理 worktree）、需要当事人 `confirm: true` 的路由（恢复、删除和导出设计，存储清理，重启和重置）、所有者的设置——姿态、防护、审批、上限、模型和计划任务，直接写入而非建议——以及打开应用的某个界面（`ui.navigate`，地址会与应用自身的界面核对，绝不是 URL）。
- **它永远不会增加的内容。** 凭据：任何密钥、令牌、密码或 OAuth 机密都不会被读取或写入；Runner、添加 MCP 服务器或登录它仍由所有者决定，密钥保管库、代理读取 `.env` 以及提供商的基础 URL 均不可写。以及谁可以访问应用：设备配对、共享、局域网入口、服务器令牌、机器人及其允许列表。
- **内核先做决定。** 审批回答的是 REVIEW；内核的 BLOCK 从不到达审批者。每次运行的检查依然有效：桥接启动的运行使用已配置的模型和姿态。
- **可见且有记录。** 开启期间，每个界面都会显示带有一键 **关闭** 的横条，以此权限进行的每次调用都会写入一条审计记录（`via: "bridge"`、`scope: "full_control"`、路由、状态和 ID——从不记录正文或值），并列在 安全 标签页中。
- **诚实的边界。** 这个开关和另外两个一样存放在 `.env` 中：能写该文件的进程无需界面即可开启它。代理的工具和桥接会拒绝该文件；以所有者身份运行的其他程序则不会。

## 所有者决定什么，仓库不能做什么

- **部署策略，只能禁止。** `<CHIMERA_HOME>/policy.json`（或 `CHIMERA_POLICY_FILE` 指定的文件）存放有序的 `provider.use` / `integration.use` / `tool.use` 规则；最后一条匹配的规则说了算。
  其中的 `allow` 从不授予任何东西：它只撤销同一文件里更早的 deny，因此不能重新打开 `CHIMERA_TOOL_DENYLIST`、可达范围下限或内核的 BLOCK。无效文件从不被部分应用：
  `serve`、`chat`、`assist`、`tui` 和 `acp` 拒绝启动，`doctor` 失败，桌面应用会启动但在修好之前拒绝所有工具。代理无法改写它——写入工具无论它放在哪里都拒绝这个文件，桥接也不能改变它的指向。
- **"在此项目中始终允许"。** 在批准卡片上，所有者可以为一个项目记住一次"同意"：按工具记，或对 shell 命令按前两个词记。之后它会回答匹配的问题，**包括运行读过不可信内容之后**——
  这是所有者 2026-10-15 的决定，明知恶意页面可能在该前缀内操纵命令。它只是跳过提问：内核的 BLOCK 和可达范围下限依然优先，带有串联、管道或重定向的命令（`;`、`&&`、`|`、`>`、`$(`…）永远不匹配。
  只有所有者能创建或撤销；桥接不能（除非所有者开启了通过桥接的完全控制，见上文）。
- **命名引用。** 所有者在 `<CHIMERA_HOME>/references.json` 中列出的文件夹，只能被读取工具（`read_file`、`list_dir`、`grep`、`glob`）以 `@alias/path` 访问；所有写入工具都拒绝别名。
  文件系统根目录、用户主目录或数据文件夹不能作为引用。只有有人值守的界面会获得它们——`chat`、`assist`、`tui`、有人值守的 `solve`、桌面的 Code 轮次（访客的除外）——所以机器人、cron 和 `scenarios` 都没有。
- **Markdown 代理。** 仓库里的 `.chimera/agents/*.md` 是别人写的文本，所以只能收窄：它的工具与会话中受治理的工具取交集，步数受会话步数的上限约束，未知键被拒绝，它的 `model` 被忽略，
  正文作为不可信数据被围起来。只有 `<CHIMERA_HOME>/agents` 里所有者自己的文件可以固定目录中的模型。
- **与对话关联的聊天。** 在 Discord 或 Telegram 中用 `/link` 可以让一个聊天接续某个 Code 对话，只有列在该机器人 allowlist 中的所有者才能关联——没有 allowlist 就谁都不能。
  关联房间里陌生人的文字永远到不了对话，媒体会被拒绝而不是转发。该轮次运行在机器人的下限上，而不是桌面的：只有 `CHIMERA_BOT_MCP_SERVERS` 中的 MCP 服务器，没有 OpenAPI 连接器，也没有 shell 授权或 host exec。
- **所有者的连接器远离陌生人能打开的门。** OpenAPI 连接器用所有者的密钥向外调用，因此会回答驱动这次运行的任何人。只要有任何已配置的机器人没有 allowlist，它就不会在消息类界面上加载；
  在 GitHub issue 任务中永远不加载（任何人都能写 issue，只校验仓库）；在 `/v1/chat/completions` 上只有设置了 `CHIMERA_SERVER_TOKEN` 时才加载。由一个模块
  `chimera/governance/surfaces.py` 按家族判断一个名字属于哪种界面（`platform:discord` 是机器人）；如果某个模块再次自己写一份列表，测试就会失败。

## 诚实的边界

这里测量的是：一个*已经被注入*的 agent，其有害动作是否会被拦下——而不是模型本身一开始能不能
被注入。对不可信文本内容的自由形式推理，以及通过合法必需的工具进行外泄，仍然是尚未解决的开放
问题（见 [issue #5](https://github.com/brcampidelli/chimera-agent/issues/5) 跟踪记录）。

完整且始终保持最新的安全政策见
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md)，其中也包括
如何报告一个漏洞。
