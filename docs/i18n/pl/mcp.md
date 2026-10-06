---
source_sha256: da279a713d209b2b6f4e14d01cd9d7fddcf07586511cb7c418edb791265112d5
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
- **`instructions` serwera** — tekst, który serwer zwraca przy `initialize`, jest odrzucany, celowo:
  to niezaufany tekst serwera i nic nie oznacza go jako danych, tak jak oznaczany jest ogrodzony
  odczyt. Kosztem jest to, że wskazówki użycia serwera nigdy nie docierają do modelu; host, który je
  przekazuje, też nie powinien na nich polegać (arXiv 2608.08467: przy dostępnym narzędziu
  wyszukiwania 9 z 24 modeli spadło poniżej 15% na zapytaniach umieszczonych w instrukcjach
  serwera). Przekazywanie ich jako ogrodzonych danych pod taintem jest otwarte, nie zrobione. Odrzucenie ich **nie** jest granicą wobec tekstu pisanego przez serwer:
  nazwy i opisy narzędzi tego samego serwera docierają do modelu tak, jak serwer je napisał, bez
  ogrodzenia; serwer, który podłączasz, to serwer, którego słowa czyta model.
- **Przypinanie manifestu** — przy pierwszym zamontowaniu serwera nazwy, opisy i input schema
  jego narzędzi są zapamiętywane w `mcp_pins.json` obok `mcp.json`. Jeśli późniejsze zamontowanie
  wypisze cokolwiek innego, serwer zostaje **wstrzymany**: aplikacja, `chimera serve` i jego boty
  (wszystko, co montuje przez wspólną pulę MCP) nie montują go, dopóki nie zatwierdzisz zmiany, ze
  starym i nowym tekstem obok siebie, przez `chimera mcp approve NAZWA` lub ekran MCP. `chimera mcp
  list` wskazuje wstrzymane serwery. Zamontowane narzędzia to lista, która została sprawdzona, więc
  serwer nie może odpowiedzieć sprawdzeniu jednym tekstem, a modelowi innym. Dodanie lub usunięcie
  serwera przez `chimera mcp add/remove` albo w aplikacji zapomina jego przypięcie (następne
  zamontowanie znów jest pierwszym kontaktem). Przypinanie to zaufanie przy pierwszym użyciu: łapie
  opis, który się **zmienia**, a nie taki, który był wrogi od początku.
  **Bez tej blokady:** powyższe API Pythona. `connect_stdio` montuje to, co serwer wypisze, i tak samo
  `autoload_into_registry`, chyba że przekażesz mu `mcp_path`, plik, którego przypięcia ma sprawdzić.
- **Sygnały wyboru** — `chimera mcp test` i ekran MCP oznaczają narzędzia frazami, które próbują
  sterować tym, które narzędzie wybierze model („always use this tool", „do not use other tools",
  „ignore previous instructions", `<IMPORTANT>`), czytanymi w opisie i w każdym opisie parametru.
  To tylko adnotacja: niczego nie odrzuca, a jak często odpala się na uczciwych serwerach, nie
  zmierzono.

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

## Pozwolenie Claude na obsługę aplikacji desktop

`chimera serve --mcp` buduje własnego agenta. `chimera mcp desktop` nie buduje niczego: to pilot
do aplikacji desktop, **którą już masz otwartą**, więc Claude widzi te same rozmowy, przebiegi i
zatwierdzenia co ty, a to, co uruchamia, działa pod nadzorem aplikacji, na jej ekranach.

1. W aplikacji otwórz **Ustawienia → Claude** i włącz **Pozwól Claude obsługiwać tę aplikację**.
2. Zarejestruj serwer w Claude Code (albo dodaj to samo polecenie do konfiguracji Claude Desktop):

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

Przy włączonym pierwszym przełączniku Claude może czytać i zaczynać rozmowy (`desktop_send`),
przebiegi, partie, tablice i zadania cron, przeszukiwać i edytować pamięć oraz czytać pliki i
stan gita. Przebieg, który uruchamia, ma postawę skonfigurowaną przez ciebie, a żądanie próbujące
ją poszerzyć — polecenie `verify`, wykonanie na hoście, inny agent, auto-zatwierdzanie — jest
odrzucane. Zatwierdzenia zostają przy tobie: `desktop_approvals` tylko je wylicza. Gdy tura
zatrzyma się na którymś, `desktop_send` od razu odpowiada, że czeka na ciebie, a tura trwa dalej
w aplikacji; `desktop_job` mówi, jak się kończy.

Drugi przełącznik, **Pełna kontrola**, dodaje `desktop_approve` (odpowiadanie na zatwierdzenia i
kroki z bramką) oraz `desktop_settings` (edycja ustawień i tożsamości agenta). Gdy jest włączony,
Claude może zatwierdzać działania bez ciebie — a strona lub wiadomość z prompt injection
przeczytana przez agenta może go do tego skłonić. Te dwa narzędzia w ogóle nie są wymieniane,
dopóki jest wyłączony, a aplikacja je odrzuca, jeśli mimo to zostaną wywołane.

Niektóre decyzje zostają twoje, niezależnie od przełącznika. Który model odpowiada — każde
ustawienie modelu, łańcuch zapasowy, panel, sędzia i syntezator fuzji, tryb kosztów, kaskada i
weryfikowane odpowiedzi — oraz czy aplikacja uruchamia zaplanowane zadania, Claude może tylko
*zasugerować*: nic nie jest zapisywane, a aplikacja pokazuje ci kartę z obecną i proponowaną
wartością każdego ustawienia, do zatwierdzenia lub odrzucenia na miejscu. Claude nie może
odpowiedzieć na tę kartę żadną drogą; jeśli ustawienie zmieniło się przed twoim zatwierdzeniem,
nic nie zostaje zastosowane. A nadanie folderowi prawa do poleceń, uruchomienie polecenia w
Runnerze, start bota komunikatora i zapisanie agenta z jego uprawnieniami do narzędzi są przez
most całkowicie odrzucane: robisz to w aplikacji.

A uruchomienie, które rozpoczyna Claude, przy każdym przełączniku działa na modelach, które
ustawiłeś, i nie sięga dalej niż ustawiona przez ciebie postawa. Prośba, która wskazuje model, plan
ról, profil, panel fuzji albo innego agenta, jest odrzucana; tak samo szersza postawa — większy
zasięg, luźniejsze zatwierdzanie, wykonanie na hoście lub polecenie `verify` tam, gdzie nie dałeś
powłoki, albo automatyczne zatwierdzanie. Poproszenie, by uruchomienie robiło mniej (tylko odczyt
albo zawsze pytaj), jest dozwolone.

Czego nie pozwala żaden przełącznik: czytać ani zapisywać klucza API, tokenu czy webhooka.
Edycje ustawień odrzucają nazwy poświadczeń, trasy niosące klucze lub linki udostępniania są
nieosiągalne, plików z poświadczeniami (`.env`, klucze prywatne) nie da się czytać, zapisywać ani
przeszukiwać, a każdy wynik jest czyszczony z wartości poświadczeń. Nie można też wskazać
workspace na folder danych aplikacji ani na folder, który go zawiera (np. katalog domowy): tam
przechowywane są odpowiedzi na zatwierdzenia, a plik zapisany w tym miejscu odpowiedziałby na
jedno z nich.

Jak się łączy: dopóki przełącznik jest włączony, aplikacja zapisuje
`~/.chimera/desktop-bridge.json` (URL swojego API na loopbacku i losowy token; w POSIX czytelny
tylko dla ciebie, w Windows w twoim profilu). Wyłączenie przełącznika lub zamknięcie aplikacji
usuwa plik i unieważnia token. Przy zamkniętej aplikacji każde narzędzie odpowiada „Chimera
desktop is not running, or 'Allow Claude to operate this app' is off in Settings.” Claude
wylicza narzędzia przy łączeniu; po włączeniu lub wyłączeniu **Pełnej kontroli** połącz serwer
ponownie (`/mcp` w Claude Code), by zobaczyć nową listę.

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
