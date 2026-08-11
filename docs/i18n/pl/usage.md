---
source_sha256: f4c7b57b8bec8e9aa96ead432d65d90113b2b11c9e24c15f7f703c2c14520786
---

# Chimera — Przewodnik użytkowania

Chimera to agent CLI-first, samo-ewoluujący, z rdzeniem rozumowania LLM-Fusion.
Ten przewodnik pokrywa instalację, konfigurację i każdą komendę z przykładami.

> Nowy w projekcie? Przeczytaj najpierw [przegląd architektury](architecture.md).

---

## Instalacja

Chimera używa [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

Każda komenda poniżej jest uruchamiana jako `uv run chimera <command>` (albo po
prostu `chimera …`, gdy virtualenv projektu jest już na twojej ścieżce PATH).

---

## Konfiguracja

Chimera jest niezależna od providera dzięki [LiteLLM](https://docs.litellm.ai/). Umieść
swoje klucze i wybory modeli w lokalnym `.env` (jest ignorowany przez git — nigdy go nie
commituj):

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

Inne przełączniki: `CHIMERA_HOME` (katalog stanu, domyślnie `.chimera`), `CHIMERA_LOG_LEVEL`
(`INFO` / `DEBUG`), `CHIMERA_CACHE` (`on`/`off`, domyślnie off — cachuje identyczne
dokończenia bez narzędzi, by pomijać powtórne wywołania API), oraz `CHIMERA_AUTO_FUSE`
(`on`/`off`, domyślnie off — automatycznie fuzjonuje głębokie lub **wrażliwe na błąd** tury w
`solve`/`crew` bez jawnego `--fuse`; router świadomy kosztów nadal utrzymuje tanie tury/tury
narzędziowe jako pojedynczy model). Router rozpoznaje prompty o dokładnej odpowiedzi (arytmetyka,
liczenie, operacje na cyfrach) w głównych językach projektu (en/pt/es/de/fr/zh/ja), więc krytyczny
krótki krok dostaje ochronę fuzji nawet wtedy, gdy jest za krótki, by uruchomić bramkę długości.

**Providerzy, fallback i self-hosted.** Działa dowolny slug LiteLLM `provider/model`
(`openai/…`, `anthropic/…`, `gemini/…`, `ollama/…`, `openrouter/…`, …). Dla serwera
self-hosted / zgodnego z OpenAI (Ollama, vLLM) ustaw `CHIMERA_API_BASE`
(np. `http://localhost:11434` z `CHIMERA_DEFAULT_MODEL=ollama/llama3`). Ustaw
`CHIMERA_FALLBACK_MODELS` (rozdzielone przecinkami), by przełączyć się na inny model, gdy
podstawowy zawiedzie. W `chat`/`tui`, `/model <slug>` przełącza model w trakcie sesji.

**Pule poświadczeń.** Daj providerowi kilka kluczy przez
`CHIMERA_<PROVIDER>_KEYS` (np. `CHIMERA_OPENROUTER_KEYS=key1,key2,key3`). Brama
rotuje je round-robin między wywołaniami (rozkładając obciążenie / limity), a
wewnątrz pojedynczego wywołania przełącza się na następny klucz, jeśli któryś zawiedzie.
Pula zastępuje pojedynczy `*_API_KEY` tego providera. *(Logowania OAuth/subskrypcyjne —
Copilot, Claude Max itp. — nie są jeszcze podpięte; klucze API i dowolny endpoint
wspierany przez LiteLLM są.)*

Sprawdź, czy wszystko jest podłączone:

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**Opcjonalne funkcje.** Widzenie (Vision), Deliverable Mode i Pet są wbudowane. Reszta
(wyszukiwanie webowe, wyszukiwanie X, generowanie obrazów, TTS/głos, Spotify, przeglądarka)
to gotowe do wypełnienia gniazda: wypełnij pasujące poświadczenie w `.env` (lub zainstaluj
zależność), a zdolność się aktywuje. `chimera features` to żywa lista kontrolna. Narzędzie
`web_search` (Tavily) rejestruje się automatycznie w momencie ustawienia `TAVILY_API_KEY` — i
jest szablonem do dodawania pozostałych (albo użyj klienta MCP / importera OpenAPI->tool).

> **Modele darmowe vs płatne.** Modele OpenRouter `:free` nic nie kosztują, ale są
> ograniczane limitami po stronie dostawcy (rate-limited) — dobre do szybkiego `run`,
> zawodne przy komendach wielowywołaniowych jak `fuse`/`solve`. Do prawdziwego użytku,
> tani płatny model (np. `deepseek/deepseek-chat-v3.1`, ułamki centa za wywołanie) jest
> dużo bardziej niezawodny.

---

## Komendy

### Status — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — interaktywny asystent wieloturowy (twoja prawa ręka)

Interaktywny REPL z pamięcią konwersacji i użyciem narzędzi — codzienny sterownik.
Przywołuje istotną pamięć długoterminową i wątkuje konwersację przez kolejne tury.

```bash
uv run chimera chat                 # start chatting; /exit to quit, /reset to clear context
uv run chimera chat --fuse          # fuse deep-reasoning turns
uv run chimera chat --no-memory     # don't recall long-term memory
```

Ten sam rdzeń konwersacyjny napędza TUI i (nadchodzącą) bramę mesagingową.

### `tui` — pełnoekranowa aplikacja terminalowa

Pełnoekranowe UI Textual nad tym samym rdzeniem konwersacyjnym. Dwa panele: **dziennik
konwersacji**, który renderuje odpowiedzi jako Markdown (kod w ogrodzeniach jest
podświetlany składniowo), z tokenami modelu **streamowanymi na żywo** w miarę ich
nadchodzenia; oraz **panel aktywności** pokazujący, co agent zrobił w tej turze — jakie
narzędzia wywołał, liczbę tokenów i koszt, i ile faktów pamięci zostało przywołanych. Te
same flagi co `chat`.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
```

Komendy: `/model <slug>` · `/reset` (wyczyść kontekst) · `/clear` (wyczyść ekran) · `/stream`
(przełącz na żywo tokeny) · `/help` · `/exit`. Klawisze: `Ctrl+R` reset · `Ctrl+L` clear ·
`Ctrl+P` paleta komend · `PgUp`/`PgDn` przewijanie · `Ctrl+C` wyjście. Komendy ze slashem
autouzupełniają się w trakcie pisania.

Uwagi o uczciwości: streamowanie tokenów działa tylko na ścieżce pojedynczego modelu — pod
`--fuse` (tura panel→judge→syntetyzator) nie ma przyrostowych tokenów, więc panel pokazuje
status "syntetyzowanie" zamiast fałszywego kursora. Koszt pokazuje "niedostępny", gdy cena
cennikowa modelu jest nieznana (nigdy nie zgadywana). Nie ma tu wskaźnika verify/revert:
verify-or-revert działa w `solve`/`project`, nie w chacie. Jeśli Textual nie jest
zainstalowany, `tui` spada do zwykłego REPL `chat`.

### `serve` — brama mesagingowa (HTTP lub Discord)

Wystawia agenta z jedną konwersacją (i jej pamięcią) **na czat**. Rdzeń routingu jest
niezależny od transportu; adaptery się podpinają.

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Każdy `chat_id` utrzymuje własny kontekst, więc różni użytkownicy/wątki się nie mieszają.

**Praca bez nadzoru (webhooki).** Zarejestruj zadanie, które odpala się na przychodzące
HTTP POST, tak by Chimera działała bez nikogo wpisującego cokolwiek — push do GitHuba,
zdarzenie Stripe, ping cron-as-a-service:

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

Ciało POST-a jest przekazywane jako kontekst zadania, i każde zadanie zarejestrowane dla
tego hooka jest uruchamiane. `GET /health` i `POST /chat` nadal działają obok tego.

**Natywny Discord.** Uruchom Chimerę jako bota Discord — każdy kanał to sesja, a agent
może też wysyłać wiadomości przez narzędzie `send_message`:

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

Stwórz bota na <https://discord.com/developers>, włącz intent **Message Content**,
i zaproś go na swój serwer. Odpowiada na każdym kanale, który widzi (odfiltrowując
własne wiadomości i wiadomości innych botów). Token jest czytany ze środowiska —
nigdy nie zaszyty na sztywno.

**Natywny Telegram.** Ten sam wzorzec adaptera, i **nie potrzebuje dodatkowej zależności**
(Telegram Bot API to zwykłe HTTP):

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**Natywny Slack.** Odbiera przez Socket Mode (potrzebuje extra `messaging`) i wysyła przez
Web API. Włącz Socket Mode w swojej aplikacji Slack, by dostać token na poziomie aplikacji:

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp (wysyłanie).** WhatsApp jest *push-based* (wiadomości przychodzą na hostowany
przez ciebie webhook Meta), więc w odróżnieniu od innych nie ma połączenia do otworzenia.
Ustaw poświadczenia Cloud API, a agent może **wysyłać** wiadomości WhatsApp przez narzędzie
`send_message` w dowolnym trybie `serve`:

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**Dwukierunkowy WhatsApp.** Skieruj webhook swojej aplikacji Meta na
`https://<your-host>/whatsapp` i ustaw `CHIMERA_WHATSAPP_VERIFY_TOKEN` (dowolny wybrany
przez ciebie string, pasujący do konfiguracji aplikacji). `chimera serve` wtedy weryfikuje
subskrypcję (`GET /whatsapp`) i kieruje wiadomości przychodzące (`POST /whatsapp`) przez
bramę, odpowiadając przez Cloud API. WhatsApp nadal potrzebuje publicznego URL-a dla
webhooka — to jedyna część spoza Chimery.

**Natywny Signal (dwukierunkowy).** Signal nie ma oficjalnego API, więc Chimera rozmawia
z mostkiem [`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api), który
uruchamiasz (Docker) i łączysz ze swoim numerem — zwykłe HTTP, bez zależności od Pythona:

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — Tier-1, pojedyncze dokończenie

Pojedyncze wywołanie modelu, bez narzędzi, bez fuzji. Najtańsza ścieżka.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**Wklejanie obrazu / widzenie.** Dołącz obrazy przez `--image` (ścieżka lub URL, można
powtarzać) — wymaga modelu ze zdolnością widzenia:

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Deliverable Mode (wyprodukuj artefakt)

Podczas gdy `run`/`chat` odpowiadają konwersacyjnie, `deliver` produkuje kompletny,
samodzielny dokument (raport, plan, spec, README...) i zapisuje go do pliku.

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — surowa pętla wywołań narzędzi ReAct

Myśl → Akcja (narzędzie) → Obserwacja, aż do finalnej odpowiedzi. Narzędzia są
ograniczone do workspace'u.

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion (wyróżnik)

Uruchamia *panel* modeli, *sędzia* (judge) analizuje ich odpowiedzi
(konsensus / sprzeczności / martwe pola), a *syntetyzator* pisze finalną
odpowiedź. Użyj `--show-panel`, by zobaczyć pełen ślad.

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

Fuzja kosztuje ~2-3× tyle co pojedyncze wywołanie, więc rezerwuj ją do trudnego
rozumowania. `fuse` wypisuje też koszt tokenów per etap (panel / judge / synth), więc
widać, gdzie faktycznie idą tokeny danego przebiegu.

**Selektywna fuzja (domyślnie WŁĄCZONA, oszczędza tokeny).** Silnik sonduje pierwsze
`CHIMERA_FUSION_PROBE_K` modeli panelu (domyślnie 2) i, gdy ich odpowiedzi ściśle się
zgadzają, pomija resztę panelu *oraz* sędziego — syntetyzując wprost ze zgadzających się
odpowiedzi. Sprawdzenie zgodności to tanie lokalne porównanie tekstu (bez dodatkowego
wywołania modelu), więc tura *niezgadzająca się* eskaluje do pełnego pipeline'u i kosztuje
dokładnie tyle samo co pełna fuzja, podczas gdy tura *zgadzająca się* jest tańsza. Dostrój
próg przez `CHIMERA_FUSION_AGREEMENT` (0–1, domyślnie 0.8), albo ustaw
`CHIMERA_FUSION_MODE=full` (lub podaj `--full`), by zawsze uruchamiać cały panel + sędziego.

Dlaczego jest domyślna: w 3 przebiegach `chimera fusion-bench --tasks hard` (płatny panel
3-modelowy) obcięła tokeny o **~20–28%** i była poprawna w **każdej** turze, którą
faktycznie skróciła (16/16). Ogólna dokładność wahała się od 0 do −8,3pp między
przebiegami, ale ta wariancja mieści się całkowicie w bucketcie *eskalowanym* — gdzie tryb
selektywny uruchamia identyczny pipeline co pełny — więc to niedeterminizm modelu, nie koszt
wczesnego zatrzymania. Uruchom benchmark na własnym obciążeniu, by zobaczyć kompromis dla
własnego panelu i zadań:

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **Wybieraj niezawodne modele panelu.** Fuzja opłaca się tylko wtedy, gdy każdy członek
> panelu faktycznie odpowiada. Unikaj slugów modeli OpenRouter `:free` w
> `CHIMERA_FUSION_PANEL` — są ograniczane limitami (HTTP 429) pod prawdziwym
> obciążeniem, a panel po cichu kurczy się do tego, co zostało z płatnych modeli. Tani,
> niezawodny tercet: `openrouter/deepseek/deepseek-chat`,
> `openrouter/openai/gpt-4o-mini`, `openrouter/meta-llama/llama-3.3-70b-instruct`.

### Karty skilli (karty rozumowania TRS, eksperymentalne)

Agent destyluje to, czego się uczy, do **kart rozumowania** — pięciu pól Trigger / Do /
Avoid / Check / Risk (plus słowa kluczowe do wyszukiwania) — zarówno z sukcesów (karta
*wzorca*), jak i powtarzających się porażek (poradniana karta *anty-wzorca*). Gdy
`CHIMERA_SKILL_CARDS=on`, `solve` przywołuje top-k istotnych kart (BM25 po nazwie +
opisie + triggerach) i wstrzykuje je do kontekstu rozumowania workera, więc agent
ponownie wykorzystuje to, co zadziałało, i unika znanych trybów porażki. To zamyka pętlę —
wcześniej nauczone skille były zapisywane i nigdy odczytywane z powrotem.

Domyślnie wyłączone: wstrzykiwanie kart dodaje tokeny promptu, a *tokenowe* oszczędności
TRS pochodzą ze skracania długich śladów rozumowania, więc na zadaniach z krótką
odpowiedzią zysk to dokładność, nie koszt. To nie jest hipotetyczne — na zestawie
krótkich odpowiedzi `hard` (płatny deepseek-v3.1), `skillcard-bench` zmierzył karty
kosztujące **+290% tokenów** i **−8pp dokładności** wobec braku kart: przy modelu blisko
sufitu i bez długiego śladu do skrócenia, generyczne karty to czysty narzut, który może
rozpraszać. Włącz karty do obciążeń **długiego rozumowania** (matematyka/kodowanie z
długimi śladami), gdzie matematyka tokenów się odwraca, i zawsze zmierz najpierw własny
kompromis kontrolą wobec ziemi odniesienia:

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

Benchmark raportuje dokładność z kartami vs bez, deltę tokenów, wskaźnik trafień kart, i
dokładność podzieloną na trafienie/pudło, z werdyktem PASS, gdy dokładność z kartami
pozostaje w granicach 1pp od baseline'u bez kart.

### Kompaktowe schematy narzędzi (eksperymentalne)

Schematy narzędzi — zwłaszcza te importowane z serwerów MCP lub specyfikacji OpenAPI —
niosą szum adnotacji (przykłady, tytuły, wartości domyślne, wieloznaniowe opisy parametrów,
zagnieżdżone ciała żądań), który jest ponownie wysyłany do modelu na **każdym** kroku
ReAct. Z `CHIMERA_COMPACT_SCHEMAS=on`, ten szum jest usuwany, a opisy parametrów przycinane
w momencie ogłaszania, **bez** dotykania niczego, co wpływa na wywołanie (nazwa i opis
funkcji, oraz `type` / `properties` / `required` / `enum` każdego schematu są zachowane).
Kanoniczne schematy pozostają nietknięte — kurczy się tylko kopia wysyłana do modelu.

Oszczędność jest największa na rozwlekłych zestawach narzędzi MCP/OpenAPI i kumuluje się
przez każdy krok; natywne narzędzia są już zwięzłe, więc ich redukcja jest mała. Zmierz
najpierw swój zestaw narzędzi (bez wywołań modelu — po prostu liczy tokeny):

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

Domyślnie wyłączone. Ponieważ kompaktowanie usuwa tylko szum adnotacji (nigdy strukturę),
jedynym ryzykiem jest to, że model ma nieco mniej prozy, by wybrać narzędzie — więc
pozostaje to konserwatywne, i powinieneś potwierdzić zachowanie wywołań narzędzi na
własnym obciążeniu przed włączeniem.

### `solve` — Tier-2 autonomiczny (plan + verify-or-revert)

Planuje zadanie, wykonuje je pętlą agenta, potem **weryfikuje wykonywalną
komendą**. Jeśli weryfikacja zawiedzie, cofa workspace i ponawia próbę z
informacją zwrotną. Weryfikator (kod wyjścia 0 = sukces) jest ziemią odniesienia.

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

Przydatne flagi:

| Flaga | Znaczenie |
|------|---------|
| `--verify "<cmd>"` | komenda, która musi zwrócić kod wyjścia 0 (testy, build, linter) |
| `--workspace`, `-w` | gdzie agent czyta/pisze (domyślnie `.`) |
| `--max-attempts N` | budżet verify-or-revert (domyślnie 3) |
| `--max-steps N` | kroki wywołań narzędzi na próbę (domyślnie 8) |
| `--fuse` | wyprodukuj **plan** przez fuzję (głębokie rozumowanie) |
| `--guard` | bramkuj każde wywołanie narzędzia przez jądro governance |
| `--no-plan` / `--no-manager` | pomiń etap planowania / przeglądu |
| `--rubric` | Manager ocenia przez **rubrykę kaskadową** (przestrzeganie instrukcji → rzetelność faktyczna → racjonalność) |
| `--no-remember` | nie zapisuj automatycznie faktu pamięci przy sukcesie |
| `--no-evolve-skills` | nie proponuj automatycznie nauczonego skilla, gdy zadanie się powtarza |
| `--isolate` | uruchom w jednorazowym git worktree; zmienione pliki kopiowane z powrotem tylko przy sukcesie |
| `--require-diff` | próba, która nie zmieniła **żadnego pliku**, kończy się porażką i jest ponawiana — dla zadania kodowego wyjaśnienie nie jest poprawką |
| `--keep-workspace` | przy porażce zostaw edycje ostatniej próby na dysku zamiast je cofać — gdy o pass/fail decyduje **zewnętrzny** oceniający |
| `--diff-feedback` | pokaż nieudanej próbie jej własny cofnięty diff, oprawiony jako droga, której nie warto powtarzać |
| `--stagnation-fuzzy` | dopasuj powtarzające się sygnatury porażek w przybliżeniu, tak by anty-stagnacyjny zwrot odpalał się na porażkach tej samej przyczyny, których sformułowanie się różni |

> **O `--max-steps`.** Domyślne 8 jest dostrojone do małych workspace'ów. Na **dużym
> repozytorium to właśnie to jest wiążącym ograniczeniem**, nie model: uruchomienie 1
> SWE-bench dało dokładne 0,0pp przy 8 krokach wobec checkoutu 250 MB, a ta sama
> konfiguracja przy **30 krokach** podniosła wskaźnik patchy baseline'u z 47% do 74%
> ([`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)). Jeśli agent eksploruje,
> a potem kończy bez edycji, podnieś to jako pierwsze.

> **`--require-diff` i `--keep-workspace` są do oceny zewnętrznej.** `solve` to
> verify-or-revert: gdy *to on* posiada decyzję pass/fail, cofnięcie nieudanej próby jest
> słuszne. Gdy posiada ją coś innego — zadanie CI, harness benchmarkowy, człowiek
> przeglądający diff — `--keep-workspace` powstrzymuje wycofanie pracy agenta, zanim ten
> sędzia w ogóle ją zobaczy, a `--require-diff` powstrzymuje pewne siebie wyjaśnienie przed
> ocenieniem go jako ukończonej zmiany. Oba są **domyślnie wyłączone**.

**`solve` uczy się między przebiegami.** Każdy przebieg zasila zamkniętą pętlę
behawioralną, całą bramkowaną przez verify-or-revert, więc tylko zweryfikowana praca ma
jakikolwiek efekt: (1) istotne **lekcje** z poprzednich prób (faworyzujące porażki) są
wplatane w plan/prompt, a **pierwszy błędny krok** nieudanej próby jest lokalizowany i
podawany do ponowienia; (2) przy zweryfikowanym sukcesie zapisywany jest zdeduplikowany
fakt **pamięci** (przywoływany później przez `chat`/`crew`); i (3) gdy wzorzec zadania się
powtarza (≥ 2 wcześniejsze sukcesy), proponowany jest wielokrotnego użytku **skill** —
przepuszczony przez panel fuzji i zachowywany przez między-modelową
**transferowalność**, gdy `--fuse` jest włączone — i zachowywany tylko wtedy, gdy przechodzi
walidację governance i wykonywalny test dymny.

### `crew` — Tier-3 wieloagentowy

Zespół agentów-ról współpracuje nad jednym zadaniem, a supervisor syntetyzuje
finalną odpowiedź.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — zespół SDLC (plan → build → test → review)

Wcześniej złożony pipeline cyklu życia oprogramowania z **verify-or-revert** na etapie
testów: `plan` dekomponuje zadanie, `build` je implementuje, `test` uruchamia
weryfikator (cofając i ponawiając build przy porażce), a recenzent krytykuje wynik.

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

Każdy etap drukuje się z ✓/✗; przebieg jest `success` tylko wtedy, gdy weryfikator etapu
testów przeszedł.

### `meta` — agenty budujące agenty

Projektuje plan (blueprint) wyspecjalizowanego agenta (nazwa, narzędzia, prompt roli) dla
zadania.

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — werdykt governance

Pokazuje decyzję jądra zaufania (allow / warn / review / block) dla działania.

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — benchmark ciągłej ewolucji

Mierzy, czy wydajność się *utrzymuje* na łańcuchu zadań (dowód anty-degradacji):
ogólny wskaźnik zdawalności, pierwsza vs. druga połowa, najdłuższa seria.

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

Raport niesie też **statystycznie uczciwą** flagę degradacji: zamiast ufać gołemu
odejmowaniu pierwsza-minus-druga-połowa (na krótkim łańcuchu wahnięcie 0,2 to zwykle
szum), `degraded_significant` wynosi `1.0` tylko wtedy, gdy przedział ufności Wilsona na
spadku wyklucza zero, `-1.0`, gdy próbka jest za mała, by cokolwiek stwierdzić, i `0.0` w
przeciwnym razie — plus granice `degradation_ci_low/high`. Osobno,
`CHIMERA_SKILL_ACCEPT_MODE=wilson` bramkuje decyzję o akceptacji skilla między modelami na
*dolnej* granicy ufności wskaźnika transferu (więc szczęśliwe 2-z-3 zdanych już się nie
liczy); domyślne `point` zachowuje surowy wskaźnik, bo granica Wilsona jest surowa na
małych panelach.

### `sandbox-bench` — ocena stanu i efektów ubocznych

Testy tekstowe oceniają *odpowiedź* modelu; ten ocenia, co agent **zrobił**. Każde
zadanie działa w izolowanym katalogu sandboksa, a harness porównuje finalny stan plików
z celem (dowolna ścieżka dozwolona, styl wynikowy) **i** osobno liczy *szkodliwe efekty
uboczne* — mutacje poza deklarowanym zbiorem dozwolonym zadania. Więc agent, który
produkuje właściwy wynik, jednocześnie niszcząc niepowiązany plik, jest wychwytywany, nie
zaliczany jako czysty sukces.

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

Raportuje `pass_rate` i `side_effect_rate`. Dostarcza *metodologię* (`StatefulTask` z
`goal_check` + zbiorem `allowed` mutacji), nie duży zestaw zadań — autoryzuj zadania dla
własnych narzędzi. Istniejące gradery tekstowe pozostają poprawne dla czystej pracy Q&A.

### `memory` — kuratorowana pamięć długoterminowa

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

Przywołanie przechodzi przez **bramkę dopuszczania** (granicę zaufania): przywołana pamięć
wchodzi do promptu tylko wtedy, gdy jest istotna *i* wolna od tekstu nadpisania/injection
(obrona przed jailbreakiem opartym na pamięci). `memory prune` zapomina pod budżetem wg
wielo-czynnikowego modelu **wartości** (świeżość, specyficzność, rodzaj, kuratorstwo,
niezawodność) — nie pojedynczej wskazówki.

**Warstwa grafu** wyciąga trójki `(source, relation, target)` z twoich pamięci
(`PassaPro uses Supabase`, `Alex prefers TypeScript`), więc fakty można przywoływać wg
encji, nie tylko wg słowa kluczowego.

### `cron` — zaplanowane zadania i SOP zdarzeniowe

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — tablica zadań z torami workerów

Tablica (`backlog → doing → review → done`), gdzie każda karta nazywa *tor*, który
kieruje ją do stosu agenta: `solve` (Tier-2 autonomiczny, verify-or-revert) lub
`crew` (Tier-3 pipeline ról). Operacyjny widok pętli, którą agent już uruchamia.

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` przeprowadza każdą kartę backlog → doing → done (sukces) lub → review (wymaga
uwagi). `learn` ponownie wykorzystuje detektor powtarzalności cron-learnera, by
kolejkować zadania, które agent powtarza (zdeduplikowane wobec tablicy) — zaplanuj to, by
automatycznie zapełniać backlog.

### `workflow` — zaprojektowane pętle (Loop Engineering)

Autoryzuj autonomiczną pętlę jako YAML zamiast doraźnego promptu. Każdy krok `uses`
(używa) zdolności (`run` / `shell` / `solve` / `crew` / `lifecycle`), może być bramkowany
na poprzednim kroku (`when: prev_succeeded | prev_failed`), i może się zapętlać (`repeat`,
`until: success`).

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

### `drift` — bramka dryfu spec↔kod

Utrzymuj spec i kod w zgodzie. Spec to mały YAML wymagań
(`defines` symbol / `contains` regex / `absent` regex / `command` zwraca kod wyjścia 0).
Bramka zwraca kod niezerowy przy dryfie, więc pełni podwójną rolę jako weryfikator.

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — import z innego agenta

Przynosi **konfigurację + skille** z Hermesa lub OpenClaw, a z `--apply` też
**scala pamięć długoterminową** (zdeduplikowaną, bez destrukcji). Domyślnie jest to
podgląd na sucho (dry-run).

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

Scalanie pamięci raportuje liczby `{ADD, UPDATE, NOOP}` — duplikaty stają się
`NOOP`, więc ponowne uruchomienie jest bezpieczne.

### `evolve` — opcjonalna ewolucja modelu (zaawansowane)

`chimera solve --collect` (domyślnie włączone) loguje każdy przebieg jako trajektorię.
Komendy `evolve` zamieniają je w zbiory danych gotowe do treningu i uruchamialny przepis
LoRA. **Trening jest zewnętrzny i opcjonalny** — zmienia wagi modelu, więc nigdy nie
dzieje się automatycznie; Chimera przygotowuje dane i skrypt i się zatrzymuje.

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` przyjmuje przełączniki przepisu: `--min-steps N` zachowuje tylko ślady
długiego horyzontu, `--diverse` zachowuje co najwyżej jeden przykład na zadanie
(różnorodność zadań jest wąskim gardłem kuratorstwa), a `--min-process P` (SkillCoach)
zachowuje tylko ślady, których wskaźnik *podążania za krokami* ≥ P — frakcja kroków
narzędzia, które wyprodukowały udany, widoczny wynik — więc szczęśliwy sukces, który
przebrnął przez nieudane wywołania narzędzi, nie jest używany do treningu. Zdarzenia
per-krok stojące za tym wskaźnikiem są przechwytywane automatycznie przy każdym
przebiegu `solve`; filtr jest domyślnie wyłączony (`CHIMERA_SFT_MIN_PROCESS` ustawia
globalną wartość domyślną). `evolve tune` jest inne niż trening — uruchamia
**meta-przeszukiwanie** nad *spec* agenta (model, prompt systemowy, budżet kroków, panel,
głębokość pamięci), oceniając każdego kandydata na codziennych scenariuszach i
zachowując edycję tylko przy **braku regresji**. Wywołuje modele, ale nigdy nie zmienia
wag, więc jest bezpieczne do uruchamiania w dowolnym momencie.

Następnie, by faktycznie trenować, na GPU (lub w Colab): `pip install chimera-agent[train]`
(albo `requirements.txt` przepisu) i `python recipe/train.py`. Skieruj
`CHIMERA_DEFAULT_MODEL` na model bazowy + adapter przy serwowaniu.

### `pet` — wirtualny towarzysz

Trwały mały towarzysz, którego statystyki dryfują, gdy cię nie ma. Bez potrzeby klucza.

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## Wskazówki

- **Narzędzia vs rozumowanie.** Tury wywołujące narzędzia zawsze używają pojedynczego
  modelu (fuzja nie może wywoływać narzędzi); fuzja jest zarezerwowana dla głębokiego
  rozumowania bez narzędzi.
- **Sprawdzaj, co się stało.** `CHIMERA_LOG_LEVEL=DEBUG` ujawnia logi routingu i
  zaangażowania fuzji.
- **Utrzymuj testy uczciwe.** Dobra komenda `--verify` (prawdziwy zestaw testów) czyni
  `solve` niezawodnym — jest to wykonywalna ziemia odniesienia, przed którą agent
  odpowiada.
