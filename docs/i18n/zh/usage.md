---
source_sha256: f4c7b57b8bec8e9aa96ead432d65d90113b2b11c9e24c15f7f703c2c14520786
---

# Chimera —— 使用指南

Chimera 是一个以 CLI 为核心、能自我演进的 agent，配有 LLM-Fusion 推理内核。
本指南涵盖安装、配置，以及每一条命令及其示例。

> 刚接触这个项目？先读一读[架构概览](architecture.md)。

---

## 安装

Chimera 使用 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

下面每一条命令都以 `uv run chimera <command>` 的形式运行（一旦项目的虚拟环境进了你的 PATH，
也可以直接写 `chimera …`）。

---

## 配置

Chimera 通过 [LiteLLM](https://docs.litellm.ai/) 实现与具体 provider 无关。把你的密钥和模型
选择放进本地的 `.env` 文件（该文件已被 git 忽略——绝不要提交它）：

```dotenv
# At least one provider key. OpenRouter unlocks 100+ models behind one key.
OPENROUTER_API_KEY=sk-or-...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Tier-1/2 default model (single, cheap, must support tool-calling for Tier-2)
CHIMERA_DEFAULT_MODEL=openrouter/deepseek/deepseek-chat-v3.1

# LLM-Fusion: a diverse panel -> judge -> synthesizer
CHIMERA_FUSION_PANEL=openrouter/deepseek/deepseek-chat-v3.1,openrouter/openai/gpt-4o-mini,openrouter/meta-llama/llama-3.3-70b-instruct
CHIMERA_FUSION_JUDGE=openrouter/deepseek/deepseek-chat-v3.1
CHIMERA_FUSION_SYNTHESIZER=openrouter/openai/gpt-4o-mini
```

其他可调选项：`CHIMERA_HOME`（状态目录，默认 `.chimera`）、`CHIMERA_LOG_LEVEL`（`INFO` /
`DEBUG`）、`CHIMERA_CACHE`（`on`/`off`，默认关闭——会缓存完全相同的、不涉及工具调用的补全
结果，以跳过重复的 API 调用），以及 `CHIMERA_AUTO_FUSE`（`on`/`off`，默认关闭——在没有显式
传入 `--fuse` 的情况下，对 `solve`/`crew` 中深度推理或**对错误敏感**的轮次自动启用融合；
成本感知路由器仍然会让廉价/工具调用轮次保持单模型）。路由器能识别项目主要支持语言
（en/pt/es/de/fr/zh/ja）中的确定性答案类提示词（算术、计数、数字运算），因此即便某个关键的
短步骤太短、触发不了长度门槛，也依然能得到融合机制的保护。

**Provider、故障转移与自托管。** 任何 LiteLLM 的 `provider/model` 标识都可用
（`openai/…`、`anthropic/…`、`gemini/…`、`ollama/…`、`openrouter/…` 等）。对于自托管 /
OpenAI 兼容的服务器（Ollama、vLLM），设置 `CHIMERA_API_BASE`（例如
`http://127.0.0.1:11434`，配合 `CHIMERA_DEFAULT_MODEL=ollama/llama3`）。设置
`CHIMERA_FALLBACK_MODELS`（逗号分隔）可以在主模型报错时自动故障转移到另一个模型。在
`chat`/`tui` 中，`/model <slug>` 可以在会话中途切换模型。

**凭据池。** 通过 `CHIMERA_<PROVIDER>_KEYS`（例如
`CHIMERA_OPENROUTER_KEYS=key1,key2,key3`）为某个 provider 提供多个密钥。网关会在多次调用间
以轮询（round-robin）方式使用它们（分摊负载/速率限制），并且在单次调用内，一旦某个密钥出错
就会故障转移到下一个密钥。一个密钥池会取代该 provider 的单一 `*_API_KEY`。*（OAuth/订阅制
登录方式——Copilot、Claude Max 等——目前尚未接入；API 密钥以及任何 LiteLLM 支持的端点则都
已接入。）*

检查一切是否配置就绪：

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**可选功能。** 视觉、Deliverable Mode（交付模式）和宠物（Pet）都是内置的。其余功能
（网页搜索、X 搜索、图像生成、TTS/语音、Spotify、浏览器）是预留好的插槽：在 `.env` 中填入
对应的凭据（或安装相应的依赖），该能力就会被激活。`chimera features` 就是一份实时的检查
清单。`web_search` 工具（Tavily）会在设置了 `TAVILY_API_KEY` 的那一刻自动完成注册——它也是
添加其他能力的范本（或者也可以使用 MCP 客户端 / OpenAPI→tool 导入器）。

> **免费模型 vs 付费模型。** OpenRouter 的 `:free` 模型不花钱，但在上游有速率限制——用来
> 快速跑一次 `run` 没问题，但用在像 `fuse`/`solve` 这类需要多次调用的命令上就不太稳定。真正
> 使用的话，一个便宜的付费模型（例如 `deepseek/deepseek-chat-v3.1`，每次调用只要几分之一
> 美分）要可靠得多。

---

## 命令

### 状态查询 —— `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` —— 交互式多轮助手（你的得力助手）

一个带对话记忆和工具调用能力的交互式 REPL——日常主力工具。它会回忆起相关的长期记忆，并把
对话内容串联在各轮之间。

```bash
uv run chimera chat                 # start chatting; /exit to quit, /reset to clear context
uv run chimera chat --fuse          # fuse deep-reasoning turns
uv run chimera chat --no-memory     # don't recall long-term memory
```

同一套对话内核也驱动着 TUI 以及（即将上线的）消息网关。

### `tui` —— 全屏终端应用

一个基于 Textual、构建在同一套对话内核之上的全屏 UI。两个面板：一个**对话记录区**，把回复渲染
为 Markdown（代码块带语法高亮），模型的 token 会**实时流式**呈现；以及一个**活动面板**，展示
agent 这一轮做了什么——调用了哪些工具、token 数量与成本，以及召回了多少条记忆事实。参数与
`chat` 相同。

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
```

命令：`/model <slug>` · `/reset`（清空上下文） · `/clear`（清屏） · `/stream`（切换实时 token
流） · `/help` · `/exit`。快捷键：`Ctrl+R` 重置 · `Ctrl+L` 清屏 · `Ctrl+P` 命令面板 ·
`PgUp`/`PgDn` 滚动 · `Ctrl+C` 退出。斜杠命令会随输入自动补全。

诚实提示：token 流式输出只在单模型路径下可用——在 `--fuse`（面板 → 评审者 → 综合器轮次）
下没有增量 token，因此面板会显示"synthesizing"（正在综合）状态，而不是伪造一个光标动画。当
某个模型的标价未知时，成本会显示为"unavailable"（不可用，绝不会去猜测）。这里没有
verify/revert（验证/回滚）指示器：verify-or-revert 只运行在 `solve`/`project` 中，不运行在
chat 里。如果没有安装 Textual，`tui` 会退回到普通的 `chat` REPL。

### `serve` —— 消息网关（HTTP 或 Discord）

以**每个会话**一条对话（及其记忆）的方式对外暴露 agent。路由内核与传输方式无关；适配器可以
即插即用。

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

每个 `chat_id` 都保有自己独立的上下文，因此不同用户/线程之间不会混在一起。

**无人值守运行（webhook）。** 注册一个在入站 HTTP POST 触发的任务，这样 Chimera 就能在没有
任何人手动输入的情况下运行——一次 GitHub push、一个 Stripe 事件、一个 cron-as-a-service
的 ping：

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

POST 请求体会作为上下文交给该任务，且所有注册在这个 hook 上的任务都会被执行。与此同时
`GET /health` 和 `POST /chat` 仍然照常可用。

**原生 Discord。** 把 Chimera 作为一个 Discord 机器人运行——每个频道就是一个会话，agent 还
可以通过 `send_message` 工具主动发送消息：

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

在 <https://discord.com/developers> 创建机器人，启用 **Message Content** intent，并把它邀请
进你的服务器。它会在它能看到的任何频道里回复（会自动过滤忽略它自己以及其他机器人发的消息）。
token 是从环境变量中读取的——绝不会硬编码在代码里。

**原生 Telegram。** 采用相同的适配器模式，而且**不需要任何额外依赖**（Telegram Bot API 就是
普通的 HTTP）：

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**原生 Slack。** 通过 Socket Mode 接收消息（需要 `messaging` extra），通过 Web API 发送消息。
在你的 Slack 应用上启用 Socket Mode，以获取一个应用级 token：

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp（发送）。** WhatsApp 是*推送式*的（消息会到达你自己托管的一个 Meta webhook），
因此和其他平台不同，这里不存在需要建立的连接。设置好 Cloud API 凭据后，agent 就能在任意
`serve` 模式下通过 `send_message` 工具**发送** WhatsApp 消息：

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**双向 WhatsApp。** 把你的 Meta 应用的 webhook 指向 `https://<your-host>/whatsapp`，并设置
`CHIMERA_WHATSAPP_VERIFY_TOKEN`（可以是你自选的任意字符串，需要和应用配置里的一致）。此后
`chimera serve` 就会验证这个订阅（`GET /whatsapp`），并把入站消息（`POST /whatsapp`）通过
网关路由处理，再经由 Cloud API 回复。WhatsApp 仍然需要一个公网 URL 来接收 webhook——这是
唯一超出 Chimera 掌控范围的部分。

**原生 Signal（双向）。** Signal 没有官方 API，因此 Chimera 会与一个你自行运行（Docker）
并绑定到你号码的 [`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api)
桥接服务通信——纯 HTTP，不需要任何 Python 依赖：

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` —— Tier-1，单次补全

一次单独的模型调用，不涉及工具，不涉及融合。是成本最低的路径。

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**视觉 / 图像粘贴。** 用 `--image` 附上图片（一个路径或 URL，可重复传入）——需要一个支持
视觉的模型：

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` —— Deliverable Mode（生成一个交付物）

`run`/`chat` 是以对话方式作答，而 `deliver` 会生成一份完整、自成一体的文档（报告、计划、
规格说明、README……）并写入文件。

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` —— 原始的 ReAct 工具调用循环

思考 → 动作（工具） → 观察，如此循环直到给出最终答案。工具的作用范围被限定在工作区内。

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` —— LLM-Fusion（差异化优势所在）

运行一个模型*面板*，一位*评审者*分析它们的答案（共识 / 矛盾 / 盲点），随后一位*综合器*写出
最终答案。用 `--show-panel` 查看完整的执行轨迹。

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

融合的成本大约是单次调用的 2-3 倍，所以要把它留给真正困难的推理任务。`fuse` 还会打印各阶段
（面板 / 评审者 / 综合器）分别花费的 token 成本，让你看清一次运行的 token 到底花在了哪里。

**选择性融合（默认开启，节省 token）。** 该引擎会先探测前 `CHIMERA_FUSION_PROBE_K` 个面板
模型（默认 2 个），如果它们的答案高度一致，就会跳过面板中剩余的模型*以及*评审者环节——直接
从这些一致的答案中进行综合。这个一致性检查只是一次廉价的本地文本比较（不需要额外的模型
调用），因此一次*出现分歧*的轮次会升级为完整流程，成本和完整融合完全一样，而一次*达成一致*
的轮次成本则更低。用 `CHIMERA_FUSION_AGREEMENT`（0–1，默认 0.8）调整这个门槛，或者设置
`CHIMERA_FUSION_MODE=full`（或传入 `--full`）以始终运行完整的面板 + 评审者流程。

为什么它是默认开启的：在 `chimera fusion-bench --tasks hard`（一个付费的三模型面板）的 3
次运行中，它把 token 消耗削减了约 **20–28%**，并且在它真正触发短路的所有轮次上（16/16）都
给出了正确答案。整体准确率在各次运行间有 0 到 −8.3 个百分点的波动，但这个波动完全落在
*被升级处理*的那部分样本里——在那部分样本上，选择性融合运行的是和完整融合完全相同的流程——
所以这是模型本身的不确定性造成的，而不是提前停止带来的代价。在你自己的负载上跑一下这个
基准，看看对你的面板和任务来说这笔交易是否划算：

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **挑选可靠的面板模型。** 只有当面板里的每个成员都能真正给出答案时，融合才能物有所值。
> 避免在 `CHIMERA_FUSION_PANEL` 中使用 OpenRouter 的 `:free` 模型标识——它们在真实负载下会
> 触发速率限制（HTTP 429），面板会在不知不觉中缩水成只剩下还能用的付费模型。一组便宜又
> 可靠的三人组：`openrouter/deepseek/deepseek-chat`、`openrouter/openai/gpt-4o-mini`、
> `openrouter/meta-llama/llama-3.3-70b-instruct`。

### 技能卡（TRS 推理卡，实验性功能）

agent 会把自己学到的东西提炼成**推理卡**——由 Trigger（触发条件）/ Do（该做什么）/
Avoid（该避免什么）/ Check（如何检查）/ Risk（风险）这五个字段（再加上用于检索的关键词）
组成——既来自成功经验（一张*模式*卡），也来自反复出现的失败（一张劝诫性质的*反模式*卡）。
当 `CHIMERA_SKILL_CARDS=on` 时，`solve` 会检索相关度最高的 top-k 张卡片（对 name +
description + triggers 做 BM25 检索），并把它们注入到 worker 的推理上下文中，让 agent 沿用
过去奏效的做法，并避开已知的失败模式。这补上了此前缺失的一环——在此之前，学到的技能只会被
存起来，从不会被读回来使用。

默认关闭：注入卡片会增加提示词 token，而 TRS 的*token* 节省来自于缩短冗长的推理轨迹，所以
在短答案任务上，它带来的收益体现在准确率上，而不是成本上。这不是纸上谈兵——在 `hard`
短答案套件（付费的 deepseek-v3.1）上，`skillcard-bench` 实测到，相比不用卡片，用了卡片会
让 token 消耗**增加 290%**、准确率**下降 8 个百分点**：在一个已经接近能力天花板、又没有
冗长轨迹可缩短的模型上，通用的卡片纯粹是负担，甚至可能造成干扰。请在**长推理**负载（带有
长推理轨迹的数学/编程任务）上启用卡片——那时 token 账本的方向会反过来——并且始终先用一个
真实基准（ground-truth check）自己测量一下这笔交易划不划算：

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

该基准会报告有卡片和无卡片时的准确率、token 差值、卡片命中率，以及按命中/未命中拆分的
准确率；当卡片准确率与无卡片基线相差不超过 1 个百分点时，会给出 PASS 判定。

### 紧凑型工具 schema（实验性功能）

工具的 schema——尤其是从 MCP 服务器或 OpenAPI 规范导入的那些——往往带有大量注解噪音
（示例、标题、默认值、多句话的参数说明、嵌套的请求体），这些内容会在**每一步** ReAct 中
被重复发送给模型。设置 `CHIMERA_COMPACT_SCHEMAS=on` 后，这些噪音会在对外呈现时被剥离、
参数说明也会被精简，**同时不会**触碰任何影响调用本身的内容（函数名和描述、以及每个 schema
中的 `type` / `properties` / `required` / `enum` 都会被保留）。规范意义上的 schema 本身
不受影响——变小的只是发给模型的那份副本。

在冗长的 MCP/OpenAPI 工具集上节省效果最明显，并且会随每一步累积；原生工具本身已经很简洁，
所以在它们身上的削减幅度较小。先测量你自己的工具集（不涉及模型调用——只是统计 token 数）：

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

默认关闭。因为压缩只会去除注解噪音（从不改动结构），唯一的风险是模型用来挑选工具的文字描述
略微变少——因此这项功能保持保守，你应当在自己的负载上确认工具调用行为之后再启用它。

### `solve` —— Tier-2 自治（规划 + 验证或回滚）

对任务进行规划，用 agent 循环执行，然后用**一条可执行命令进行验证**。如果验证失败，就回滚
工作区并带着反馈重试。验证器（exit code 0 = 成功）就是事实依据。

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

常用参数：

| 参数 | 含义 |
|------|---------|
| `--verify "<cmd>"` | 必须返回退出码 0 的命令（测试、构建、代码检查等） |
| `--workspace`, `-w` | agent 读写的位置（默认 `.`） |
| `--max-attempts N` | verify-or-revert 的尝试预算（默认 3） |
| `--max-steps N` | 每次尝试中的工具调用步数（默认 8） |
| `--fuse` | 通过融合来产出**规划**（深度推理） |
| `--guard` | 让每一次工具调用都经过治理内核把关 |
| `--no-plan` / `--no-manager` | 跳过规划 / 复核阶段 |
| `--rubric` | Manager 通过**级联评分标准**进行判定（指令遵循 → 事实性 → 合理性） |
| `--no-remember` | 成功后不自动写入一条记忆事实 |
| `--no-evolve-skills` | 任务重复出现时不自动提议学到的技能 |
| `--isolate` | 在一个用后即弃的 git worktree 中运行；只有成功时才会把改动过的文件拷回来 |
| `--require-diff` | 一次没有改动**任何文件**的尝试视为失败并重试——对代码任务而言，一段解释不算修复 |
| `--keep-workspace` | 失败时把最后一次尝试的改动留在磁盘上而不回滚——适用于由**外部**评分者判定通过/失败的场景 |
| `--diff-feedback` | 把一次失败尝试自己被回滚掉的 diff 展示给它看，将其框定为一条不要再走的路 |
| `--stagnation-fuzzy` | 对重复出现的失败特征做近似匹配，这样即便措辞不同，只要是同一原因造成的失败，也能触发防停滞的转向机制 |

> **关于 `--max-steps`。** 默认值 8 是为小型工作区调优的。在一个**大型仓库**中，这才是真正
> 的限制因素，而不是模型本身：SWE-bench 第 1 轮实验在 8 步的设置下、针对一个 250 MB 的
> checkout 拿到了精确的 0.0 个百分点，而同样的配置在改成**30 步**之后，把基线的补丁通过率
> 从 47% 提升到了 74%（[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)）。如果
> agent 探索了一通却没有做任何编辑就收尾了，先调大这个参数。

> **`--require-diff` 和 `--keep-workspace` 是为外部评分场景准备的。** `solve` 本身是
> verify-or-revert 的：当*它自己*掌握通过/失败的判定权时，回滚一次失败的尝试是正确的做法。
> 但当判定权在别处——一个 CI 任务、一个基准测试脚手架、一个正在审阅 diff 的人——
> `--keep-workspace` 能防止 agent 的工作成果在那个评判者看到之前就被回滚掉，而
> `--require-diff` 能防止一段自信满满的解释被误判为一次完成的改动。这两者**默认都是关闭**
> 的。

**`solve` 会跨多次运行不断学习。** 每一次运行都会反馈进一个闭合的行为循环，且全程受
verify-or-revert 把关，因此只有经过验证的工作才会产生任何效果：（1）从过往尝试中提取出的
相关**经验教训**（优先考虑失败案例）会被融入规划/提示词中，而一次失败尝试的**第一个出错
步骤**会被定位出来并反馈进重试环节；（2）在一次通过验证的成功之后，会写入一条去重后的
**记忆**事实（供之后的 `chat`/`crew` 召回）；以及（3）当某种任务模式反复出现（此前已成功
≥ 2 次）时，会提议一项可复用的**技能**——在启用 `--fuse` 时会跨融合面板评估，并依据跨模型
的**可迁移性**来决定是否保留——并且只有在通过治理校验和一次可执行的冒烟测试之后才会被保留。

### `crew` —— Tier-3 多智能体

一支由角色 agent 组成的团队协作完成一项任务，再由一位 supervisor 综合出最终答案。

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` —— SDLC 团队（规划 → 构建 → 测试 → 复核）

一条预先组装好的软件生命周期流水线，在测试阶段带有 **verify-or-revert**：`plan` 分解任务，
`build` 实现它，`test` 运行验证器（失败时回滚构建结果并重试），最后由一位复核者对结果进行
点评。

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

每个阶段都会打印出 ✓/✗；只有当测试阶段的验证器通过时，整次运行才算 `success`。

### `meta` —— agent 构建 agent

为某个任务设计一份专用 agent 的蓝图（名称、工具、角色提示词）。

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` —— 治理判定结果

展示信任内核对某个动作的判定结果（允许 / 警告 / 复核 / 阻止）。

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` —— 持续演进基准测试

衡量性能在一连串任务中是否*保持得住*（反性能退化的证据）：整体通过率、前半段与后半段对比、
最长连胜纪录。

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

该报告还带有一个**统计上诚实**的退化标志：与其直接相信"后半段减前半段"这种粗暴的减法（在
一条较短的任务链上，0.2 的摆动通常只是噪音），只有当针对这个降幅的 Wilson 置信区间不包含
零时，`degraded_significant` 才会取值 `1.0`；当样本量太小、无法下结论时取 `-1.0`；其余情况
则取 `0.0`——同时还会给出 `degradation_ci_low/high` 这两个置信区间边界。另外，
`CHIMERA_SKILL_ACCEPT_MODE=wilson` 会让跨模型的技能采纳决策以迁移率的*置信区间下界*为准
（这样一次侥幸的 3 中 2 次通过就不再算数了）；默认的 `point` 模式则保留原始比率，因为
Wilson 区间对很小的面板来说过于严格。

### `sandbox-bench` —— 状态与副作用评分

前面的文本类基准评的是模型的*答案*；这一个评的是 agent 实际*做了什么*。每个任务都在一个
隔离的沙箱目录中运行，脚手架会把最终的文件状态与目标进行对比（允许任意路径，按结果打分），
**并且**单独统计*有害的副作用*——即发生在该任务声明允许范围之外的改动。这样一来，一个虽然
得出了正确结果、却顺带破坏了一个不相关文件的 agent 就会被抓出来，而不会被记为一次干净的
通过。

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

报告 `pass_rate`（通过率）和 `side_effect_rate`（副作用率）。它提供的是一套*方法论*
（一个带 `goal_check` + `allowed` 允许改动集合的 `StatefulTask`），而不是一个大型任务套件——
需要你为自己的工具编写任务。现有的文本评分器在纯问答类工作上依然是正确适用的。

### `memory` —— 经过筛选的长期记忆

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

记忆召回会经过一道**准入关卡**（一条信任边界）：一条被召回的记忆只有在既相关、又不含
覆盖/注入类文本时才会进入提示词（针对基于记忆的越狱攻击的防御）。`memory prune` 依据一个
多因子的**价值**模型（新近度、具体程度、种类、是否经过整理、可靠性）在预算内决定遗忘哪些
记忆——而不是只依赖单一线索。

**图谱层**会从你的记忆中提取出 `(source, relation, target)`（源、关系、目标）三元组
（例如 `PassaPro uses Supabase`、`Alex prefers TypeScript`），因此事实不仅能按关键词召回，
也能按实体召回。

### `cron` —— 定时任务与事件 SOP

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` —— 带 worker 泳道的任务看板

一块看板（`backlog → doing → review → done`，待办 → 进行中 → 复核 → 完成），每张卡片都指定
一条*泳道*，把它派发给对应的 agent 能力层：`solve`（Tier-2 自治，verify-or-revert）或
`crew`（Tier-3 角色流水线）。这就是 agent 本就在运行的那套循环的操作性视图。

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` 会让每张卡片依次走完 backlog → doing → done（成功）或 → review（需要人工关注）。
`learn` 复用 cron-learner 的重复检测器，把 agent 反复执行的任务加入队列（会与看板上已有的
卡片去重）——可以把它安排为定时任务，用来自动填充 backlog。

### `workflow` —— 设计好的循环（Loop Engineering）

用 YAML 而不是随手写的提示词来编写一个自主循环。每个步骤都会 `uses`（使用）某项能力
（`run` / `shell` / `solve` / `crew` / `lifecycle`），可以依赖上一步的结果作为门槛
（`when: prev_succeeded | prev_failed`），也可以循环执行（`repeat`、`until: success`）。

```yaml
# examples/workflow.yaml
name: build-and-report
steps:
  - name: build
    uses: solve
    with: { task: "Create greeting.py with greet(name)", verify: "python -c \"import greeting\"" }
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with: { prompt: "One-line changelog for greet()" }
```

```bash
uv run chimera workflow examples/workflow.yaml --workspace ./scratch
```

### `drift` —— spec↔代码 漂移关卡

让一份 spec（规格说明）和代码保持一致。一份 spec 是一小段 YAML 格式的需求描述（`defines`
定义某个符号 / `contains` 包含某个正则匹配 / `absent` 不存在某个正则匹配 / `command` 某条
命令退出码为 0）。当出现漂移时该关卡会以非零状态退出，因此它也可以兼作验证器使用。

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` —— 从另一个 agent 导入

把**配置 + 技能**从 Hermes 或 OpenClaw 中带过来，加上 `--apply` 参数后还会**合并长期记忆**
（去重、非破坏性）。默认是一次仅预览的 dry-run。

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

记忆合并会报告 `{ADD, UPDATE, NOOP}` 三种计数——重复项会变成 `NOOP`，因此重复运行是安全的。

### `evolve` —— 可选启用的模型演进（进阶功能）

`chimera solve --collect`（默认开启）会把每次运行都记录为一条轨迹。`evolve` 系列命令能把
这些轨迹变成可用于训练的数据集，以及一份可直接运行的 LoRA 训练方案。**训练本身是外部进行、
且需要主动开启的**——因为它会改变模型权重，所以绝不会自动发生；Chimera 只负责准备数据和
脚本，然后就此止步。

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` 支持一些方案调节参数：`--min-steps N` 只保留长跨度的轨迹，`--diverse` 每个任务最多
只保留一个样本（任务多样性才是数据整理环节的瓶颈），而 `--min-process P`（SkillCoach）只保留
*按步骤遵循度*得分 ≥ P 的轨迹——即产出了成功、可见结果的工具调用步骤所占的比例——这样一次
中间经历了大量失败工具调用、侥幸成功的案例就不会被拿去训练。这个得分背后的逐步事件会在每次
`solve` 运行时自动被记录下来；这个过滤器默认关闭（`CHIMERA_SFT_MIN_PROCESS` 用来设置一个
全局默认值）。`evolve tune` 与训练不同——它对 agent 的*规格*（模型、系统提示词、步数预算、
面板、记忆深度）做一次**元搜索**，在每日场景上给每个候选方案打分，只有在**没有出现退化**时
才会保留某项改动。它会调用模型，但从不改变权重，因此随时运行都是安全的。

之后，如果要真正进行训练，在一台 GPU 上（或 Colab 上）：安装 `pip install
chimera-agent[train]`（或使用该方案自带的 `requirements.txt`），然后运行
`python recipe/train.py`。提供服务时，把 `CHIMERA_DEFAULT_MODEL` 指向基础模型 + 适配器
（adapter）。

### `pet` —— 一个虚拟伙伴

一个会持续存在的小伙伴，即便你不在的时候，它的各项状态值也会随时间发生变化。不需要任何
密钥。

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## 使用小贴士

- **工具调用 vs 推理。** 工具调用轮次始终使用单一模型（融合无法调用工具）；融合是留给
  不涉及工具的深度推理任务的。
- **查看到底发生了什么。** `CHIMERA_LOG_LEVEL=DEBUG` 会显示路由和融合触发相关的日志。
- **让测试保持诚实。** 一条好的 `--verify` 命令（一套真正的测试套件）能让 `solve` 变得
  可靠——它就是 agent 必须对齐的、可执行的事实依据。
