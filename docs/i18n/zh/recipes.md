---
source_sha256: f08c31cf980c0d86795fe456d5f9ed6871b58325c9e429e93f48c71b6e998356
---

# 配方（Recipes）

一批真实、可直接运行的工作流，用内置工具端到端完成一些有用的事情。用
`chimera workflow <file> -w <workspace>` 运行其中任何一个。完整源码见
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples) 目录。

## 完全本地运行，无需 API 密钥（Ollama）

在你自己的机器上、针对本地模型运行 Chimera——不需要密钥，任何数据都不会离开这台机器。安装
[Ollama](https://ollama.com)，拉取一个模型，然后把 Chimera 指向它：

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera run "Summarise this file in 3 bullets" -w .
```

就这么简单——不需要 `OPENROUTER_API_KEY`，也不涉及云端。凭据检查会把 `ollama/…`（以及
`ollama_chat/…`）识别为本地运行时并直接放行。如果 Ollama 运行在别处，设置
`CHIMERA_OLLAMA_BASE_URL=http://host:11434`（默认是 `http://localhost:11434`）。

本地模型体量更小，因此这属于[适度区间（goldilocks）](../bench/local_lift/RESULTS.md)里*偏弱*
的一端——很适合 `chimera solve`（规划 + 验证或回滚能帮到一个较弱的模型），也适合追求离线隐私
的场景，但不太适合单轮就要求前沿水平推理的任务。你也可以混搭：本地模型作默认，遇到困难的调用
再走云端的 `CHIMERA_FALLBACK_MODELS`。

## 邮件分诊

读取你的收件箱，把邮件分类为 `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`，并写出一份十秒钟
就能看完的摘要。整个过程只读——不删除、不移动、不发送任何邮件。

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

需要 IMAP 凭据。设置方式及每日定时运行：
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md)。

## 每日研究简报

输入一个主题，输出一份带 5 个要点、附来源的简报，外加一段 3 行的摘要（arxiv 总会用到；如果设
置了 Tavily 密钥则还会用到网页搜索）。

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## 仓库看门狗

运行某个仓库的测试套件，并写出一份健康报告，列出所有失败的测试。除了写报告之外全程只读。

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## 阅读文档（PDF、DOCX、XLSX……）

agent 开箱即用地能读取纯文本。要处理真正的文档——PDF、Word、PowerPoint、Excel、HTML、CSV、
EPUB——安装可选的 extra，它就会获得一个 `read_document` 工具，能把上述任意一种格式转换成
Markdown：

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

然后把一个任务指向某个文件：*"把 report.pdf 概括成 5 个要点。"* 没有安装这个 extra 时，
`read_document` 会返回一行安装提示，而不是直接失败。

## 浏览网页（导航、阅读并操作）

`browser` 工具是**内置**的——它驱动一个真实的 Chromium（因此它能看到普通的 `http_get` 看不到的、
由 JavaScript 渲染出来的页面）。Playwright 随 Chimera 一起提供；约 150MB 的 Chromium 二进制文件
会在你**第一次使用浏览器工具时自动下载**（这是 pip 无法替你完成的一次性步骤）。不需要任何额外
安装步骤：

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

`browser` 工具支持以下动作：

- **`navigate` / `read`** —— 打开一个 URL，并把页面上*可交互*的元素列成 `[ref] role: name`
  （链接、按钮、输入框），这样 agent 就能按 `ref` 而不是按像素坐标去点击/输入。
- **`read_text`** —— 页面**渲染后的完整文本**，用于阅读/研究一篇文章、文档或搜索结果。装了
  `documents` extra 后会得到干净的 **Markdown**（通过 MarkItDown 保留标题、链接、列表结构）；
  没装的话就是纯可见文本。可以传入一个可选的 `url` 参数，一步完成"打开 + 阅读"。
- **`find`** —— 在渲染出的文本中搜索某个关键词，返回匹配到的行。
- **`click` / `type` / `back`** —— 按 `ref` 操作页面。

`CHIMERA_BROWSER_HEADLESS=false` 会以有头（headful）模式运行 Chromium，便于调试。

页面内容**不可信**：每一条结果都会被数据围栏（data-fenced），并且该工具会给这次运行打上污点，
因此浏览网页时优先使用 `solve --taint --guard`，需要提取结构化字段时走隔离读取器
（quarantined reader），而不要直接对原始页面文本采取行动。没有 `documents` extra 时，
`read_text` 依然能用——只是返回纯文本而不是 Markdown。

## 研究一个主题（搜索 + 阅读）

把网页搜索和浏览器的 `read_text` 结合起来，研究某个主题并得到一份附来源的简报——`web_search`
（需要 `TAVILY_API_KEY`）负责找到相关页面，`browser read_text` 逐一阅读它们（包括
JS 重度依赖的网站），`deliver` 负责把简报写出来：

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

如果想要一个每一步都带可执行检查的现成版本，参见
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief)
——它开箱即用地使用 `arxiv_search` + `web_search`，而在装了 `browser` extra 之后，agent 还能
`read_text` 完整页面，而不只是停留在搜索结果摘要上。

## 抓取与安全的结构化提取

两个内置工具能把任意页面变成干净、可直接喂给 LLM 的数据——无需安装任何额外组件：

- **`scrape`** —— 抓取一个 URL，返回干净的 **Markdown + 元数据**。它会走一条成本感知的级联
  路径：先尝试普通的 HTTP GET，如果页面返回为空则升级为内置的 **browser**（JS 渲染），并且——
  仅当你设置了 `FIRECRAWL_API_KEY` 时——才会兜底回退到 **Firecrawl** 处理反爬严重的页面。
  `render=http|browser|firecrawl` 可以强制指定某个后端；`include_links` 还会一并返回页面上的
  链接。
- **`extract`** —— 安全地把指定字段提取为**经过校验的 JSON**。给它一个 `url`（或 `content`）
  和一份 `fields` 列表（例如 `["title", "price", "author"]`），它就只会返回这些字段。关键在于，
  它是通过 Chimera 的**隔离读取器**（quarantined reader）来读取页面的——这是一个没有工具权限、
  输出会经过 schema 校验的模型——因此**隐藏在页面里的指令无法劫持 agent**。这正是
  Firecrawl/ScrapeGraphAI 无法给你的安全保证：一个恶意页面最坏情况下也只能返回一个错误的值，
  绝不可能变成一条新指令。大页面会被分块处理再合并，一旦所有字段都被填满就会提前停止以控制
  成本。对于**已知的页面模板**，可以传入 `selectors`（字段 → CSS 选择器，例如
  `{"price": ".price", "link": "a.more::attr(href)"}`），这些字段就会被**确定性地提取——免费、
  不经过 LLM**——只有选择器没能填上的字段才会用到那个安全的 LLM。

```bash
uv run chimera run "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera run "extract the fields title, price, availability from https://example.com/product --taint"
```

针对整个站点，还有两个动词可用：

- **`map`** —— 低成本地列出一个站点的 URL（有 sitemap 时读取它，没有的话就扫描页面上的链接）。
  可选的 `search` 关键词参数用于过滤结果。在爬取一个站点之前先用它来确定范围。
- **`crawl`** —— 从一个种子 URL 出发追踪链接，返回每个页面清洗后的 Markdown。受 `limit` 和
  `max_depth` 约束，默认只在同一域名内，并且**遵守 robots.txt**（服从 `Disallow` 和
  `Crawl-delay`）。`include`/`exclude` 是 URL 的 glob 匹配模式。长时间的爬取是**可续跑的**：
  待爬队列（frontier）会在每爬完一页后写入磁盘检查点，因此一次在第 N 页被中断的爬取，下次运行
  会从第 N+1 页继续（默认 `resume=true`）。

```bash
uv run chimera run "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

所有内容都会被数据围栏并给这次运行打上污点（因为这些都是不可信的网页内容），因此
`solve --taint --guard` 是对其采取行动的安全方式。可选的 Firecrawl 兜底方案*只有*在内置引擎
无法抓取某个页面、且设置了密钥时才会被使用——Chimera 自己就能抓取绝大多数网页，不依赖外部
服务。

## 音频：语音转文字（转录）

Chimera 能把语音转成文字——是它图像生成和文本转语音工具的对称能力。它**编排调用一个 Whisper
模型**（而不是自己训练一个）：如果安装了 `stt` extra，`transcribe_audio` 工具会使用本地的
**faster-whisper**（离线/私密）；否则就使用托管的 OpenAI Whisper API（需要一个 OpenAI 密钥）：

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera run "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> 关于能力边界的说明，秉持本项目一贯的诚实态度：Chimera 是一个 **agent**，而不是一个模型。它
> 可以*调用*语音转文字、图像生成、计算机视觉或经典机器学习——通过调用某个 API，或在其代码沙箱
> 中运行某个库——但它不会（也不应该）去*重新实现* Whisper、Stable Diffusion、PyTorch 或
> OpenCV。至于数据科学 / 机器学习，`execute_code` 沙箱本身就已经允许 agent 编写并运行针对
> scikit-learn、pandas、OpenCV 等库的 Python 代码。编排调用能放大 agent 的能力；重新实现则只会
> 产出一份更慢的复制品。

## 下载视频或其音轨

`download_media` 工具能把一段视频（或仅其音轨）从 YouTube 及其他 1000 多个网站下载到工作区
中。它包装了 **yt-dlp**（持续维护，能应对 cipher/格式/年龄限制之类的频繁变动，而这些正是
pytube 这类单站点抓取工具容易失效的地方）。这是可选安装的；提取音轨还需要 PATH 中有
`ffmpeg`：

```bash
uv sync --extra media-dl
uv run chimera run "download the audio of https://youtu.be/… then transcribe it and summarize"
```

和上面的 `transcribe_audio` 天然搭配：下载 → 转录 → 总结，一次运行全部完成。

## 数据分析 / 机器学习（`data_analysis` 技能）

Chimera 不会重新实现 scikit-learn——它会**编写正确的 pandas/sklearn 代码，并在**
`execute_code` 沙箱中**运行它**。`data_analysis` 技能正是这项能力的名字：给它一个任务和一份
数据集，它就会生成一段自成一体的脚本（加载 → 探索 → 建模 → 评估），随后由 agent 执行它。

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera run "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## 图像生成（云端托管或完全本地）

`generate_image` 默认使用 OpenAI 的图像 API。要实现**离线 / 私密**的部署方式，设置
`CHIMERA_IMAGE_BACKEND=local` 并安装（较重、依赖 GPU 的）`imagegen-local` extra——此后 Chimera
会通过 `diffusers` 在本地运行 **FLUX.1-schnell**（Apache-2.0 协议）。`auto`（默认值）只有在
没有 OpenAI 密钥时才会使用本地方式。

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera run "generate an image of a fox in a snowy forest"
```

> 和上文一样诚实地划定范围：Chimera 在这里*运行*一个扩散模型，而不是训练一个。视频生成
> （例如 CogVideo）是有意**不**内置的——那是一个重量级的、已训练好的模型，不适合让 agent
> 在其基础包里携带；真有需要时，去调用某个托管 API 即可。计算机视觉（OpenCV）不需要专门的
> 工具——agent 在代码沙箱里已经可以直接 `import cv2`。

## 图表与数据可视化

有两种互补的方式来制作图表——两者对自身的能力边界都很诚实（Chimera 是在*使用*绘图库，而不是
重新实现 matplotlib/plotly/bokeh）：

**1. `data_visualization` 技能——编写图表代码，在沙箱中运行它。** 覆盖*一切*场景（自定义/出版
级图表、3D、任何形式）：该技能会生成一段自成一体、使用 matplotlib/seaborn（静态 PNG/SVG）或
plotly（交互式 HTML）的脚本，并内置了无头后端设置（`matplotlib.use("Agg")`）以及保存到工作区
的规范。

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera run "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. `render_chart` 工具——一份安全、声明式的 Vega-Lite 规范。** 一份 Vega-Lite 规范是**惰性
的 JSON 数据，而不是代码**：可检查、有明确 schema 形状、可重新渲染——对于 Vega-Lite 能覆盖的
标准图表（柱状图/折线图/散点图/直方图/热力图/分面图……）而言，这比直接执行生成出来的代码提供了
更强的治理保障。**HTML 输出不需要任何额外组件**（它会内嵌规范本身以及 Vega 的 CDN 链接）；
PNG/SVG 输出则需要可选的 `viz-vega` extra（`vl-convert-python`）。

```bash
uv run chimera run "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> 诚实的能力边界：plotly 包装的是 plotly.js，bokeh 大约有一半是 TypeScript，matplotlib 的渲染
> 器是用 C++ 写的，而 seaborn 只是 matplotlib 上薄薄的一层封装——这些框架都应该被 agent
> *调用*，而不是重写。代码沙箱本身已经能导入它们；这项技能只是给这个能力起了个名字，并处理好
> 无头模式下的各种坑。Vega-Lite 是个例外，因为它产出的制品是安全的声明式数据，值得为它单独做
> 一个工具。

## 把它们全部安排为定时任务

任何一份配方都可以放到 cron 上运行，并把结果送达到聊天工具：

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

关于消息网关和 7×24 全天候部署，参见[部署](deploy.md)。
