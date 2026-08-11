---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
---

# Podłączanie serwerów MCP

MCP (Model Context Protocol) to standardowy sposób podłączania zewnętrznych narzędzi do agenta —
mówi nim GitHub, systemy plików, Notion, bazy danych i setki innych serwerów. Chimera ma
pierwszorzędnego klienta MCP: narzędzia dowolnego serwera stają się zwykłymi narzędziami Chimery,
siedzącymi w tym samym rejestrze co wbudowane, i podlegają tym samym warstwom
allowlisty/jądra/rejestru (ledger).

## Zainstaluj extra klienta

Klient MCP żyje za opcjonalnym extra, żeby rdzeń pozostał lekki:

```bash
uv sync --extra mcp
```

Większość serwerów to pakiety Node, więc potrzebujesz też `npx` (dostarczany z Node.js).

## 60-sekundowy test dymny (bez poświadczeń)

Referencyjny serwer systemu plików nie potrzebuje żadnych tokenów — po prostu wystawia narzędzia
odczytu/zapisu nad wybranym przez ciebie katalogiem:

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

Podaj ten rejestr `Agentowi` (albo zobacz `examples/mcp_github.py` po pełną pętlę), a model
może teraz wywoływać narzędzia serwera jak każde inne.

## Prawdziwy serwer: GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

To cała integracja: ~26 narzędzi GitHub (przeszukiwanie repozytoriów, czytanie plików,
wypisywanie issues, tworzenie PR-ów, ...) pojawia się w rejestrze. Uruchamialna wersja
end-to-end:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## Jak to pasuje do warstw bezpieczeństwa

Narzędzia MCP są zwykłymi obiektami `Tool`, więc wszystko się komponuje:

- **Allowlista na sesję** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  przyznaje tylko te narzędzia MCP, których potrzebuje dane uruchomienie; nieprzyznane nigdy nie
  docierają do modelu.
- **Jądro governance** — `govern_registry(...)` bramkuje wywołania MCP allow/warn/review/block
  jak każdą komendę shellową.
- **Rejestr skażenia (taint ledger)** — owiń przez `ledger_registry(...)`, a pobrania MCP są
  rejestrowane; zauważ, że dziś tylko narzędzia wymienione w `FETCH_TOOLS` są auto-klasyfikowane,
  więc traktuj treść MCP jako niezaufaną i preferuj uruchamianie z semantyką `--taint --guard`,
  gdy serwer pobiera dane zewnętrzne.

## Chimera *jako* serwer MCP

Klient powyżej pozwala Chimerze wywoływać inne narzędzia. Odwrotność też działa: uruchom Chimerę
**jako** serwer MCP, tak by dowolny klient MCP — Claude Desktop, IDE, inny agent — mógł wywołać
cały silnik jako trzy narzędzia.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

Wystawia:

| Narzędzie | Co robi |
| --- | --- |
| `chimera_solve` | Autonomicznie rozwiązuje zadanie z planem + verify-or-revert; zwraca odpowiedź. |
| `chimera_fuse` | Odpowiada na prompt przez silnik LLM-Fusion (panel → judge → syntetyzator). |
| `chimera_memory_search` | Przeszukuje pamięć długoterminową Chimery i zwraca najważniejsze fakty. |

Skieruj klienta MCP na to jako serwer stdio. Dla Claude Desktop, dodaj do jego konfiguracji:

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` potrzebuje klucza providera do `chimera_solve`/`chimera_fuse` (przeszukiwanie pamięci
działa bez niego). Dodaj `--fuse`, by kierować głębokie tury solvera przez fuzję, `--no-memory`,
by pominąć przywoływanie pamięci. Ponieważ nośnikiem jest stdio, wszystkie logi idą do stderr —
stdout niesie tylko protokół.

## Mówienie A2A (agent → agent)

MCP łączy agenty z *narzędziami*; **A2A** (Agent2Agent, Linux Foundation) łączy agenty
*ze sobą nawzajem* — jest natywne w LangGraph, CrewAI i AutoGen. Chimera mówi tym też, więc
orkiestrator LangGraph/CrewAI może zdelegować zadanie do Chimery i dostać z powrotem ukończony
wynik.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` dodaje dwie trasy do serwera HTTP:

| Trasa | Cel |
| --- | --- |
| `GET /.well-known/agent.json` | Karta agenta (Agent Card) — tożsamość + reklamowane umiejętności (solve, fuse). |
| `POST /a2a` | Cykl życia zadania JSON-RPC 2.0: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

Klient wysyła `message/send` z częścią tekstową; Chimera uruchamia autonomicznego agenta i
zwraca zadanie `completed` (lub `failed`) niosące odpowiedź jako wiadomość agenta. Albo wysyła
`message/stream` i dostaje strumień **Server-Sent Events**: najpierw zadanie w stanie `working`,
potem zadanie `completed`/`failed`, gdy przebieg się skończy — więc orkiestrator widzi postęp
bez odpytywania (polling). Karta agenta reklamuje `capabilities.streaming: true`.

**Zakres, uczciwie:** strumień obecnie emituje dwa zdarzenia (working → final), nie delty
tokenów per krok, a powiadomienia push nie są zaimplementowane. To zgodny, wolny od
odpytywania strumień — wystarczający, by być pełnoprawnym strumieniowalnym węzłem w aplikacji
LangGraph/CrewAI.

## Rozwiązywanie problemów

- `TimeoutError: MCP server ... did not become ready` — komenda się nie uruchomiła. Uruchom tę
  samą linię `npx ...` ręcznie w terminalu, by zobaczyć jej błąd (brakujący token, brakujący
  Node, wolne pobieranie pakietu przy pierwszym uruchomieniu — podnieś `connect_timeout`).
- `ModuleNotFoundError: mcp` — zainstaluj extra: `uv sync --extra mcp`.
- Kolizje nazw narzędzi — zawsze podawaj `name_prefix`.
- Sesja uruchamia serwer jako podproces na czas życia twojego skryptu; wywołaj `close()` na
  sesji `connectora` (albo po prostu pozwól procesowi się zakończyć), by go zdemontować.
