---
source_sha256: bec93da7acb7d3a5290f324d0095c1da6c043ab5acb014a8cbcf4a63ada31510
---

# 在服务器（VPS）上部署 Chimera

Chimera 作为一个长期运行的**网关**（gateway）进程运行。加上 `--cron`，它还会按照真实时钟触发
计划任务，因此它是*到点自己行动*，而不仅仅是被消息触发才响应。本指南介绍两种在一台 5 美元 VPS
上的部署方式：**Docker Compose**（推荐）或 **systemd**。

状态——长期记忆、定时任务（cron）、轨迹（trajectories）、审计日志——都保存在 `CHIMERA_HOME`
（一个目录）里。只要把它持久化下来（一个 Docker 卷，或一个真实路径），agent 重启后状态也能保留。

---

## 0. 前置条件

- 一台 Linux VPS（单个 agent 的话，1 vCPU / 1 GB 内存就足够）。
- 至少一个 provider 密钥。最便宜的起步方式是使用 OpenRouter 密钥。
- 如果要接收公网入站 webhook（WhatsApp Cloud API，`POST /webhook/<hook>`），需要一个域名 + 带
  TLS 的反向代理（Caddy 或 nginx）。Discord/Telegram/Slack/Signal 是出站连接，不需要这些。

从模板创建你的 env 文件并填入一个密钥：

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose（推荐）

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

这会运行 `chimera serve --host 0.0.0.0 --cron`：即 HTTP 网关（`/chat`、`/webhook/<hook>`、
`/health`）**加上** cron 守护进程。状态会持久化保存在 `chimera-data` 卷中。

**接入一个聊天平台**（以 Discord 为例）——在 `.env` 中设置好 token，然后在 `docker-compose.yml`
中覆盖启动命令：

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

再执行一次 `docker compose up -d`。（Telegram/Slack/Signal 的接法一样，通过各自的标志启用；每
个平台都需要对应的 `CHIMERA_*` token——参见 `.env.example`。）

**升级到新版本：**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd（不使用 Docker）

在宿主机上安装到一个虚拟环境（virtualenv）中：

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

创建 `/etc/systemd/system/chimera.service`：

```ini
[Unit]
Description=Chimera Agent gateway + cron daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/chimera
EnvironmentFile=/opt/chimera/.env
Environment=CHIMERA_HOME=/opt/chimera/state
ExecStart=/opt/chimera/.venv/bin/chimera serve --host 0.0.0.0 --cron
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chimera
sudo systemctl status chimera
journalctl -u chimera -f
```

---

## 3. 安排主动任务（`--cron` 守护进程）

`--cron` 只会*运行*你已经安排好的任务。用 CLI 添加它们（会持久化保存在 `CHIMERA_HOME` 中）：

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

在 Docker 内部：

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

守护进程每 `--cron-tick` 秒（默认 30 秒）检查一次，到点就把对应任务的动作通过 agent 派发执行。
某个任务失败只会被记录下来，不会让守护进程停止运行。

### 向计划表问出它没有告诉你的事

```bash
chimera cron doctor
```

计划表安静下来有两种方式，而在你开口问之前，两者看起来一模一样——都像是一份眼下没有任何任务到点
的计划表：

- **什么都没跑过。** 守护进程死了，容器一直没被重启，宿主机睡着了。没有异常，没有日志行，没有
  判定。这里其他所有的诚实机制都位于*一次执行确实发生过*的下游，所以谁都轮不上。
- **全都跑了，也全都输了。** 守护进程活着，`last_run` 就在一分钟前，计划表也在往前推进——可每一
  次派发都已经失败了整整一个月。这种情况读起来反而比第一种*更健康*，因为那个看上去像健康指标的
  字段，记录的是尝试，而不是结果。

`cron doctor` 会把这两个问题都问一遍，并给出不同的建议，因为这两者的修法毫无共同之处：逾期是守
护进程的事，失败是任务本身的事。只要有任何东西在失败，`chimera cron list` 就会打印出一行，所以
你不必事先知道有这条命令才能发现问题。

**它不是什么。** 它是一个问题，不是一个监视者：进程躺下的这段时间里，这里没有任何东西会察觉，原
因和一个已经崩溃的进程无法记录自己的崩溃是一样的。只要有任何东西来问——一个 shell、桌面应用、下
一次启动——它就会诚实地回答。真正的看门狗需要自己的时钟和自己的存活性，那是另一个独立的决定，
[已作为 issue #26 跟踪](https://github.com/brcampidelli/chimera-agent/issues/26)。如果你要的是告
警而不是回答，就从宿主机自己的 cron 里运行它：

```cron
*/30 * * * * cd /opt/chimera && .venv/bin/chimera cron doctor | mail -s "chimera" you@example.com
```

这之所以有效，正是因为看着它的是 Chimera 之外的东西——这就是全部要点。

---

## 4. 健康检查、备份、安全

- **健康检查：** `GET /health` 会返回 `{"ok": true}`。Compose 已经接好了健康检查。
- **备份：** 备份 `chimera-data` 卷（Docker 方式）或 `CHIMERA_HOME` 目录（systemd 方式）——这就
  是全部的持久化状态。示例：`docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf
  /b/chimera-state.tgz -C /d .`
- **密钥：** 把密钥留在 `.env` 中（已被 git 忽略）；绝不要把它们打进镜像里。
- **暴露面：** 只在防火墙/反向代理之后才把网关绑定到 `0.0.0.0`。设置
  **`CHIMERA_SERVER_TOKEN`**，要求 HTTP 网关和桌面端 API 都必须带上
  `Authorization: Bearer <token>`（桌面 UI 只会在回环/loopback 客户端上自动拿到这个 token，因此
  一个暴露在公网上的实例仍然会受到你自己的鉴权保护）。鉴权是可选启用的，默认为空，因此不设置
  这个变量就等于没有鉴权——请务必限制端口访问，或者只暴露 webhook 路径。要从桌面应用连到这个实
  例，参见 [§5](#5-reaching-this-instance-from-the-desktop-app)。
- **沙箱化：** 设置 `CHIMERA_SANDBOX=docker`，让 shell/代码工具在一个用后即弃的容器中运行，而
  不是直接在宿主机上运行。
- **无人值守的宿主机执行：** 自 2026-07-20 起，在默认的 `CHIMERA_HOST_EXEC=ask` 设置下，无头
  （headless）运行会**拒绝**执行宿主机命令（因为没有 TTY 可供确认）。如果某个部署确实需要
  agent 在宿主机上运行 shell，需要有意地设置 `CHIMERA_HOST_EXEC=allow`；更安全的做法是使用
  `CHIMERA_SANDBOX=docker`，此时这道关卡会被跳过，因为容器本身就已经真正做到了隔离。同样地，
  API 服务器还会启用污点收紧（`CHIMERA_TAINT_NARROW=1`）：agent 一旦读取了不可信内容，后续的
  执行/写入/外发类工具就会失败并采取保守（fail-closed）策略。设为 `0` 则可以让它继续自主行动。

---

## 5. 从桌面应用连到这个实例

桌面应用默认只和它自己在你机器上启动的那个 Chimera 对话。从 v0.44 起，它也可以指向一个由你自己
运行的实例——就是这台 VPS——于是这个应用就成了一扇窗，让你看见那个整夜都在替你跑定时任务的
agent。

**在开放端口之前，请先读这一段。** 你暴露出去的并不是一块仪表盘。这个应用的每一个界面都是操作
面：它运行 shell、编辑文件、派发一整块自主任务看板，还会修改设置。一个没有 token、却能从公网访
问到的实例，不是「一个别人只能看看的 Chimera」——它是一台机器，任何找到地址的人都能在上面执行命
令，而账单记在你的 provider 密钥上。

有三件事必须成立，而前两件不成立时，应用会直接拒绝连接：

**1 —— TLS。** 把它放到一个带有真实证书的反向代理后面（Caddy 会替你申请一张）：

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

只要地址不在你自己的机器上，应用就会拒绝非 `https` 的地址，因为 token 会随**每一个**请求放在
`Authorization` 头里一路走过去——走明文 http 的话，那就是一份交给你与服务器之间每一跳的凭据，而
这一切发生时，屏幕上看不出任何不对劲。

**2 —— 一个 token。** 鉴权是可选启用的，默认为空：

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

把它写进 `.env`，重启，再把同一个值粘贴到应用里。应用之所以拒绝没有 token 的远程地址，就是上面
那个理由：没有 token 的实例对任何找到它的人都是敞开的。

请注意服务器刻意**不做**的一件事：当远程客户端请求界面时，它送出的页面里*不带* token。token 从
不会经由网络发放——你在带外把它复制到自己的客户端里，只此一次。这就是应用里有那个输入框的原因。

**3 —— 你这个应用的来源。** 应用是由它自己的本地 sidecar 提供的，所以它发往这个实例的请求属于跨
来源请求；只要这个实例没有点名那个来源，浏览器就会把响应丢掉：

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

连接失败时，应用会把确切的值显示给你——它就在那条错误信息里，可以直接复制。端口对每一次安装都是
固定的（自 v0.43 起会在多次启动之间记住），所以每台你用来连接的机器只需设置一次。多个来源之间用
逗号分隔。

**这项设置不是安全边界，也绝不能被当成安全边界来读。** CORS 决定的是哪个*页面*可以读取响应；它
对谁可以发起*调用*不作任何决定。关卡是 token。只点名来源而不设置 token，什么都保护不了——那只会
让一个没有防护的实例除了 `curl` 之外，也能从浏览器访问到。

默认为空，所以一个没人配置过的实例，行为和以前完全一样。

### 失败时应用会告诉你什么

- **「token 被拒绝了」**——地址和来源都对，是值错了。
- **「连不上」**——要么地址错了，要么来源没被允许。浏览器故意不肯说是哪一种，所以应用不去猜，而
  是把两者都点出来，并把需要放行的来源直接交给你。
- **版本警告**——应用会拿它自己后端的版本和这一端比较，并把两个数字都说出来。它不会拒绝：落后一
  个版本的服务器通常照样能用，而拒绝只会把你挡在恰好用来修这件事的界面之外。较旧的一端可能没有
  某些端点。

### 还可以更安全

干脆完全跳过公网端口：通过 WireGuard 或者 Tailscale 的 tailnet 连到这台 VPS，再把应用指向内网地
址。token 依然重要——tailnet 只是一间更小的屋子，不是一间空屋子。

---

## 6. 诚实的现状说明

Chimera 目前处于 **alpha** 阶段。它可以完成部署并正常运行，cron 守护进程也让它具备了主动行为
能力——但目前还**没有生产环境的实际运行里程**。请先从低风险的定时任务开始，密切关注 `logs`，
并在处理任何涉及真实系统的任务时，牢记这些治理护栏（`solve` 上的 `--guard`、
`CHIMERA_SANDBOX=docker`）。

## 这些页面发布在哪里

这些文件就是 **chimeraagent.space** 网站文档的源文件，该网站在构建时会直接从这个目录渲染出
页面。在这里编辑 markdown，网站内容就会随之更新；不存在需要另外同步的第二份副本。

曾经放在 `mkdocs.yml` 里的 MkDocs 配置已被移除。它本身是完整的——主题、导航、十个页面齐全——
但从未真正发布过：既没有对应的工作流（workflow），也没有 `gh-pages` 分支，所以原本写在这个
位置的部署说明，描述的其实是一个并不存在的网站。没有人在运行的配置，比压根没有配置还糟糕，
因为下一个接手的人会去改它的导航结构，却怎么也想不明白为什么什么都没有变化。
