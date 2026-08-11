---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# 扩展 Chimera

Chimera 从设计上就是可扩展的。本指南展示教会它新本领的四种方式——一个**工具**（tool）、一个
**技能**（skill）、一份**配方**（recipe），或一个**外部集成**——每一种都配有完整、可直接复制
粘贴使用的示例。不需要对代码库有深入了解。

刚接触这个项目？先读一读[使用指南](usage.md)和[架构概览](architecture.md)。准备好提交改动了？
参见 [CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md)。

| 我想要… | 就添加一个… | 位置在 |
|---|---|---|
| 给 agent 一个新的**动作**（调用某个 API、执行一次计算、操作某个设备） | **工具（Tool）** | `chimera/tools/` |
| 打包一个可复用、依托模型的**流程/提示词**，供 agent 复用并不断改进 | **技能（Skill）** | `chimera/skills/builtin/` |
| 用一个文件描述并自动化一套**多步骤例程** | **配方（Recipe，即 workflow YAML）** | `examples/` |
| 无需 fork 就接入一个**已存在的外部工具/服务器** | **MCP 服务器** | [mcp.md](mcp.md) |

---

## 1. 添加一个工具

**工具**是 agent 可以采取的一个单一动作。继承 `Tool`，设置三个属性，并实现 `run` 方法——它
**总是返回一个字符串**（绝不抛出异常；遇到问题就返回 `"error: …"`）。这就是全部契约。

```python
# chimera/tools/weather.py
from __future__ import annotations

from typing import Any

from chimera.tools.base import Tool


class WeatherTool(Tool):
    name = "weather"
    description = "Get the current temperature for a city (demo tool)."
    parameters = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name, e.g. 'Lisbon'."},
        },
        "required": ["city"],
    }

    def run(self, **kwargs: Any) -> str:
        import httpx  # lazy import: keep tool construction cheap

        city = str(kwargs["city"]).strip()
        if not city:
            return "error: 'city' is required"
        try:
            geo = httpx.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1}, timeout=15,
            ).json()
            if not geo.get("results"):
                return f"error: city not found: {city}"
            lat, lon = geo["results"][0]["latitude"], geo["results"][0]["longitude"]
            wx = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params={"latitude": lat, "longitude": lon, "current": "temperature_2m"},
                timeout=15,
            ).json()
            return f"{city}: {wx['current']['temperature_2m']}°C"
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return f"error: weather lookup failed: {exc}"
```

**注册它**，让 agent 能够使用——在 `default_registry`（`chimera/tools/builtin.py`）里加上一行：

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**测试它**（不联网——Chimera 的测试是封闭的；在边界处打桩/mock）：

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**让一个工具保持可合并（mergeable）的约定：**
- `run` 返回一个**字符串**，绝不抛出异常——把 I/O 包在 `try/except` 里，出错就返回
  `"error: …"`。
- 把较重的依赖**惰性导入（lazy-import）**在 `run` 内部，而不是放在模块顶部。
- 不要把密钥写进代码里——从环境变量中读取（参见 `chimera/config.py`）。
- 如果工具读取的是不可信内容（网页、文件），在 `--taint` 模式下运行时会被自动**数据围栏
  （data-fenced）**并**做污点追踪（taint-tracked）**；不要自己重新造这个轮子（参见
  [security.md](security.md)）。

---

## 2. 添加一个技能

**技能**是一段可复用、依托模型的流程——是 agent 按相关性呈现、并由演进引擎日后加以精炼的
"增强版工具"层级。继承 `LLMSkill`，设置 `name` / `description` / `version`，并返回一个
`SkillResult`。

```python
# chimera/skills/builtin/naming_skills.py
from __future__ import annotations

from typing import Any

from chimera.skills.base import SkillResult
from chimera.skills.llm_skill import LLMSkill


class NameThingSkill(LLMSkill):
    """Suggest short, memorable names for a project or product."""

    name = "name_thing"
    description = "Suggest 5 short, brandable names for a described project."
    version = "0.1.0"

    def run(self, **kwargs: Any) -> SkillResult:
        about = kwargs.get("about")
        if not isinstance(about, str) or not about.strip():
            return SkillResult(ok=False, error="missing required string 'about'")
        system = "You name things. Reply with exactly 5 short names, one per line, no numbering."
        text = self.ask(system=system, user=about)   # LLMSkill helper: one model call
        return SkillResult(ok=True, output=text)
```

在 `chimera/skills/builtin/__init__.py` 中注册它（沿用那里已有的写法）。技能会带有**使用指
标**和一套由*实测*成功率驱动的**生命周期**（provisional 试用 → active 启用 → retired 淘汰）——
参见 [`chimera/evolution/`](../chimera/evolution)——因此一个不再有用的技能会被自动降级，而不是
靠猜测判断。

> **该用工具还是技能？** **工具**做的是某种确定性的事情（一次 API 调用、一次文件编辑）。
> **技能**是一段*调用模型的、带提示词的流程*。有副作用的动作选工具；可复用的推理/生成选技能。

---

## 3. 添加一份配方（workflow）

**配方**无需任何代码就能自动化一套多步骤例程——一个由 agent 通过 `chimera workflow` 执行的
YAML 文件。非常适合用作定时任务。真实示例见 [`examples/`](../examples)（邮件分诊、晨间简报、
仓库看门狗）。

每个步骤都会 `uses`（使用）agent 技术栈的某项能力（`run`、`solve`、`crew` 等）；
`when: prev_succeeded` 让某一步依赖于上一步是否成功，`repeat` 加 `until: success` 则会对某一步
进行重试。

```yaml
# my_flow.yaml — build a small module, then write a changelog only if it built
name: build-and-report
steps:
  - name: build
    uses: solve
    with:
      task: "Create greeting.py with greet(name) returning 'Hello, ' + name."
      verify: "python -c \"import greeting; assert greeting.greet('x') == 'Hello, x'\""
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with:
      prompt: "Write a one-line changelog entry for adding greeting.greet()."
```

```bash
chimera workflow my_flow.yaml
# schedule it to run every morning at 8:
chimera cron add digest "0 8 * * *" "chimera workflow my_flow.yaml"
```

---

## 4. 连接一个外部工具（MCP）

要使用一个**已经存在**的工具——一台数据库服务器、一个 SaaS 集成、别人写好的工具包——你不需要
fork Chimera。只要把它指向任意一个 **MCP 服务器**，它的工具就会和原生工具一起出现。完整指南 +
一个可直接运行的示例：**[mcp.md](mcp.md)**。Chimera 自己也可以*作为* MCP 服务器
（`chimera serve --mcp`），这样其他 agent 就能使用*它*的工具。

---

## 提交 PR 之前

请运行和 CI 一样的质量关卡——必须全部通过：

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

为你新增的任何内容都写上测试，保持 `run` 返回字符串（工具）或 `SkillResult`（技能），并在 PR
中说明**改了什么 / 为什么改 / 怎么测试**。诚实、经过测量的改动才是标准——如果一项改动声称带来
了改进，就把数字亮出来。感谢你让 Chimera 变得更好。🧬
