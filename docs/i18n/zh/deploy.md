---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
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
  这个变量就等于没有鉴权——请务必限制端口访问，或者只暴露 webhook 路径。
- **沙箱化：** 设置 `CHIMERA_SANDBOX=docker`，让 shell/代码工具在一个用后即弃的容器中运行，而
  不是直接在宿主机上运行。
- **无人值守的宿主机执行：** 自 2026-07-20 起，在默认的 `CHIMERA_HOST_EXEC=ask` 设置下，无头
  （headless）运行会**拒绝**执行宿主机命令（因为没有 TTY 可供确认）。如果某个部署确实需要
  agent 在宿主机上运行 shell，需要有意地设置 `CHIMERA_HOST_EXEC=allow`；更安全的做法是使用
  `CHIMERA_SANDBOX=docker`，此时这道关卡会被跳过，因为容器本身就已经真正做到了隔离。同样地，
  API 服务器还会启用污点收紧（`CHIMERA_TAINT_NARROW=1`）：agent 一旦读取了不可信内容，后续的
  执行/写入/外发类工具就会失败并采取保守（fail-closed）策略。设为 `0` 则可以让它继续自主行动。

---

## 5. 诚实的现状说明

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
