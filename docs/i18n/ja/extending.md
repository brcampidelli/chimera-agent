---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Chimeraを拡張する

Chimeraは拡張されることを前提に作られています。このガイドでは、新しいことを教える4つの方法 — **tool**、**skill**、**recipe**、**外部統合** — をそれぞれコピー&ペーストできる完全な例とともに示します。コードベースの深い知識は必要ありません。

このプロジェクトが初めての方は、まず[利用ガイド](usage.md)と[アーキテクチャ概要](architecture.md)をお読みください。変更を送る準備ができたら、[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md)を参照してください。

| やりたいこと | 追加するもの | 場所 |
|---|---|---|
| エージェントに新しい**アクション**を与える(APIを呼ぶ、計算を実行する、デバイスを操作する) | **Tool** | `chimera/tools/` |
| エージェントが再利用し改善できる再利用可能な**手順/プロンプト**をパッケージ化する | **Skill** | `chimera/skills/builtin/` |
| ファイルに記述された**複数ステップのルーチン**を自動化する | **Recipe**(ワークフローYAML) | `examples/` |
| フォークせずに**既存の外部ツール/サーバー**を接続する | **MCPサーバー** | [mcp.md](mcp.md) |

---

## 1. toolを追加する

**tool**はエージェントが取れる単一のアクションです。`Tool` をサブクラス化し、3つの属性を設定し、`run` を実装します — これは**常に文字列を返し**(決して例外を発生させず)、問題は `"error: …"` として報告します。それが契約のすべてです。

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

**登録する**とエージェントが使えるようになります — `default_registry`(`chimera/tools/builtin.py`)に1行追加してください。

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**テストする**(ネットワークなし — Chimeraのテストはヘルメティック(隔離)です。境界をモックしてください)。

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**toolをマージ可能に保つための規約:**
- `run` は**文字列**を返し、決して例外を発生させません — I/Oを `try/except` で包み、`"error: …"` を返してください。
- 重い依存関係はモジュールのトップではなく `run` 内で**遅延インポート**してください。
- シークレットをコードに含めないでください — 環境変数から読み込んでください(`chimera/config.py` を参照)。
- toolが信頼できないコンテンツ(ウェブページ、ファイル)を読む場合、`--taint` の下で実行すると自動的に**データが囲い込まれ**、**汚染追跡**されます。それを再発明しないでください([security.md](security.md)を参照)。

---

## 2. skillを追加する

**skill**は再利用可能な、モデルに支えられた手順です — エージェントが関連性によって表面化させ、後で進化エンジンが洗練させる「拡張されたtool」の階層です。`LLMSkill` をサブクラス化し、`name` / `description` / `version` を設定し、`SkillResult` を返します。

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

`chimera/skills/builtin/__init__.py` に登録してください(既存のパターンに従ってください)。skillは**利用指標**と、*計測された*成功によって駆動される**ライフサイクル**(provisional → active → retired)を持ちます — [`chimera/evolution/`](../chimera/evolution) を参照してください — そのため、役に立たなくなったskillは、当て推量ではなく自動的に降格されます。

> **toolかskillか?** **tool**は決定論的な何か(API呼び出し、ファイル編集)を*実行*します。**skill**はモデルを使う*プロンプト化された手順*です。副作用のあるアクションにはtoolを、再利用可能な推論/生成にはskillを選んでください。

---

## 3. recipe(ワークフロー)を追加する

**recipe**はコードなしで複数ステップのルーチンを自動化します — エージェントが `chimera workflow` で実行するYAMLファイルです。スケジュールされたジョブに最適です。実例は[`examples/`](../examples)にあります(メールトリアージ、朝のブリーフ、リポジトリウォッチドッグ)。

各ステップはエージェントスタックのケーパビリティ(`run`、`solve`、`crew` など)を `uses` します。`when: prev_succeeded` はステップを前のステップに条件付けし、`repeat` + `until: success` はステップを再試行します。

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

## 4. 外部tool(MCP)を接続する

**すでに存在する**tool — データベースサーバー、SaaS統合、他人のツールキット — を使うには、Chimeraをフォークする必要はありません。任意の**MCPサーバー**を指定すれば、そのtoolがネイティブのものと並んで表示されます。完全なガイド+実行可能な例: **[mcp.md](mcp.md)**。ChimeraはMCPサーバーに*なる*こともできるので(`chimera serve --mcp`)、他のエージェントが*Chimeraの*toolを使うこともできます。

---

## PRを開く前に

CIが実行するのと同じゲートを実行してください — それは緑でなければなりません。

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

追加するものすべてにテストを追加し、`run` は文字列(tool)または `SkillResult`(skill)を返すようにし、PRには**何を/なぜ/どうテストするか**を記述してください。正直で計測された変更が基準です — 変更が改善を主張するなら、その数字を示してください。Chimeraをより良くしてくれてありがとうございます。🧬
