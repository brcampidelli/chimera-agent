---
source_sha256: 9b7904ffeb685c16ca9394893e8e02621c5031b5600c9735b81d3abe623f7531
---

# Estendendo o Chimera

O Chimera foi construído para ser estendido. Este guia mostra as quatro formas de ensinar algo
novo a ele — uma **tool**, uma **skill**, uma **recipe**, ou uma **integração externa** — cada uma
com um exemplo completo, pronto para copiar e colar. Não é preciso conhecimento profundo da base
de código.

Novo no projeto? Leia o [Guia de Uso](usage.md) e a
[visão geral da Arquitetura](architecture.md). Pronto para enviar uma mudança? Veja
[CONTRIBUTING.md](https://github.com/brcampidelli/chimera-agent/blob/main/CONTRIBUTING.md).

| Eu quero… | Adicionar uma… | Onde |
|---|---|---|
| Dar ao agente uma nova **ação** (chamar uma API, rodar um cálculo, tocar um dispositivo) | **Tool** | `chimera/tools/` |
| Empacotar um **procedimento/prompt** reutilizável que o agente pode reusar e melhorar | **Skill** | `chimera/skills/builtin/` |
| Automatizar uma **rotina de múltiplos passos** descrita em um arquivo | **Recipe** (workflow YAML) | `examples/` |
| Plugar uma **tool/servidor externo já existente** sem fazer fork | **Servidor MCP** | [mcp.md](mcp.md) |

---

## 1. Adicionar uma tool

Uma **tool** é uma única ação que o agente pode tomar. Faça subclasse de `Tool`, defina três
atributos, e implemente `run` — que **sempre retorna uma string** (nunca levanta exceção; relate
problemas como `"error: …"`). Esse é o contrato inteiro.

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

**Registre-a** para que o agente possa usá-la — adicione uma linha em `default_registry`
(`chimera/tools/builtin.py`):

```python
from chimera.tools.weather import WeatherTool
registry.register(WeatherTool())
```

**Teste-a** (sem rede — os testes do Chimera são herméticos; faça mock da fronteira):

```python
# tests/test_weather_tool.py
from chimera.tools.weather import WeatherTool

def test_weather_requires_city():
    assert WeatherTool().run(city="  ").startswith("error:")
```

**Convenções que mantêm uma tool mergeável:**
- `run` retorna uma **string** e nunca levanta exceção — envolva I/O em `try/except` e retorne
  `"error: …"`.
- Faça **import preguiçoso** de dependências pesadas dentro de `run`, não no topo do módulo.
- Mantenha segredos fora do código — leia-os do ambiente (veja `chimera/config.py`).
- Se a tool lê conteúdo não confiável (páginas web, arquivos), ele é automaticamente
  **cercado como dado** e **rastreado por taint** quando rodado sob `--taint`; não reinvente isso
  (veja [security.md](security.md)).

---

## 2. Adicionar uma skill

Uma **skill** é um procedimento reutilizável, apoiado em modelo — o tier de "tool aumentada" que o
agente exibe por relevância e que o motor de evolução depois refina. Faça subclasse de
`LLMSkill`, defina `name` / `description` / `version`, e retorne um `SkillResult`.

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

Registre-a em `chimera/skills/builtin/__init__.py` (siga o padrão já existente lá). As skills
carregam **métricas de uso** e um **ciclo de vida** (provisional → active → retired) guiado por
sucesso **medido** — veja [`chimera/evolution/`](../chimera/evolution) — então uma skill que para
de ajudar é rebaixada automaticamente, nunca por suposição.

> **Tool ou skill?** Uma **tool** *faz* algo determinístico (uma chamada de API, uma edição de
> arquivo). Uma **skill** é um *procedimento com prompt* que usa o modelo. Escolha uma tool para
> ações com efeitos colaterais; uma skill para raciocínio/geração reutilizável.

---

## 3. Adicionar uma recipe (workflow)

Uma **recipe** automatiza uma rotina de múltiplos passos sem nenhum código — um arquivo YAML que o
agente executa com `chimera workflow`. Ótimo para jobs agendados. Veja exemplos reais em
[`examples/`](../examples) (triagem de e-mail, resumo matinal, watchdog de repositório).

Cada passo `uses` uma capacidade da pilha do agente (`run`, `solve`, `crew`, …); `when:
prev_succeeded` condiciona um passo ao anterior, e `repeat` + `until: success` fazem um passo
tentar de novo.

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

## 4. Conectar uma tool externa (MCP)

Para usar uma tool que **já existe** — um servidor de banco de dados, uma integração SaaS, o
toolkit de outra pessoa — você não precisa fazer fork do Chimera. Aponte-o para qualquer
**servidor MCP** e as tools dele aparecem ao lado das nativas. Guia completo + um exemplo
executável: **[mcp.md](mcp.md)**. O Chimera também pode *ser* um servidor MCP
(`chimera serve --mcp`), para que outros agentes possam usar *as tools dele*.

---

## Antes de abrir um PR

Rode o mesmo gate que o CI roda — precisa estar verde:

```bash
uv run --no-sync ruff check .          # style + lint
uv run --no-sync mypy chimera          # types (strict)
uv run --no-sync pytest -q             # tests (hermetic: no network)
```

Adicione um teste para tudo que você adicionar, mantenha `run` retornando strings (tools) ou
`SkillResult` (skills), e descreva **o quê / por quê / como testar** no PR. Mudanças honestas e
medidas são a régua — se uma mudança alega uma melhoria, mostre o número. Obrigado por tornar o
Chimera melhor. 🧬
