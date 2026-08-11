---
source_sha256: f4c7b57b8bec8e9aa96ead432d65d90113b2b11c9e24c15f7f703c2c14520786
---

# Chimera — Nutzungsleitfaden

Chimera ist ein CLI-first, sich selbst weiterentwickelnder Agent mit einem LLM-Fusion-
Reasoning-Kern. Dieser Leitfaden deckt Installation, Konfiguration und jeden Befehl mit
Beispielen ab.

> Neu im Projekt? Zuerst den [Architektur-Überblick](architecture.md) lesen.

---

## Installation

Chimera nutzt [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

Jeder Befehl unten wird als `uv run chimera <command>` ausgeführt (oder einfach
`chimera …`, sobald die Virtualenv des Projekts im PATH liegt).

---

## Konfiguration

Chimera ist über [LiteLLM](https://docs.litellm.ai/) providerunabhängig. Keys und
Modellwahl in eine lokale `.env` legen (sie ist git-ignoriert — nie committen):

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

Weitere Stellschrauben: `CHIMERA_HOME` (Zustandsverzeichnis, Standard `.chimera`),
`CHIMERA_LOG_LEVEL` (`INFO` / `DEBUG`), `CHIMERA_CACHE` (`on`/`off`, standardmäßig
aus — cacht identische werkzeuglose Completions, um wiederholte API-Aufrufe zu
überspringen) und `CHIMERA_AUTO_FUSE` (`on`/`off`, standardmäßig aus — fusioniert
automatisch tiefe oder **fehlersensible** Turns in `solve`/`crew` ohne explizites
`--fuse`; der kostenbewusste Router hält günstige/Tool-Turns weiterhin bei einem
einzelnen Modell). Der Router erkennt Prompts mit exakter Antwort (Arithmetik,
Zählen, Ziffernoperationen) in den Hauptsprachen des Projekts (en/pt/es/de/fr/zh/
ja), sodass ein kritischer kurzer Schritt den Schutz der Fusion erhält, selbst wenn
er zu kurz ist, um das Längen-Gate auszulösen.

**Provider, Fallback & selbst gehostet.** Jeder LiteLLM-`provider/model`-Slug
funktioniert (`openai/…`, `anthropic/…`, `gemini/…`, `ollama/…`, `openrouter/…`,
…). Für einen selbst gehosteten / OpenAI-kompatiblen Server (Ollama, vLLM)
`CHIMERA_API_BASE` setzen (z. B. `http://localhost:11434` mit
`CHIMERA_DEFAULT_MODEL=ollama/llama3`). `CHIMERA_FALLBACK_MODELS`
(kommagetrennt) setzen, um bei einem Fehler des primären Modells auf ein anderes
auszuweichen. In `chat`/`tui` wechselt `/model <slug>` das Modell mitten in der
Sitzung.

**Credential-Pools.** Einem Provider mehrere Keys geben mit
`CHIMERA_<PROVIDER>_KEYS` (z. B. `CHIMERA_OPENROUTER_KEYS=key1,key2,key3`). Das
Gateway rotiert sie Round-Robin über die Aufrufe hinweg (verteilt Last/Rate-
Limits) und weicht innerhalb eines einzelnen Aufrufs auf den nächsten Key aus,
wenn einer fehlschlägt. Ein Pool ersetzt den einzelnen `*_API_KEY` dieses
Providers. *(OAuth-/Abo-Logins — Copilot, Claude Max usw. — sind noch nicht
verdrahtet; API-Keys und jeder von LiteLLM unterstützte Endpunkt sind es.)*

Prüfen, ob alles verdrahtet ist:

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**Optionale Funktionen.** Vision, Deliverable Mode und das Pet sind eingebaut.
Der Rest (Web-Suche, X-Suche, Bildgenerierung, TTS/Stimme, Spotify, Browser)
sind vorbereitete Slots: das passende Credential in `.env` eintragen (oder die
Abhängigkeit installieren), und die Fähigkeit aktiviert sich. `chimera features`
ist die lebende Checkliste. Das `web_search`-Tool (Tavily) registriert sich
automatisch, sobald `TAVILY_API_KEY` gesetzt ist — und ist die Vorlage für das
Hinzufügen der anderen (oder den MCP-Client/OpenAPI→Tool-Importer nutzen).

> **Freie vs. bezahlte Modelle.** OpenRouter-`:free`-Modelle kosten nichts, sind
> aber upstream ratenlimitiert — gut für einen schnellen `run`, wackelig für
> Multi-Call-Befehle wie `fuse`/`solve`. Für echten Einsatz ist ein günstiges
> bezahltes Modell (z. B. `deepseek/deepseek-chat-v3.1`, Bruchteile eines Cents
> pro Aufruf) weit zuverlässiger.

---

## Befehle

### Status — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — interaktiver Multi-Turn-Assistent (deine rechte Hand)

Ein interaktives REPL mit Gesprächsgedächtnis und Tool-Nutzung — der tägliche
Treiber. Es ruft relevantes Langzeitgedächtnis ab und führt das Gespräch über
Turns hinweg fort.

```bash
uv run chimera chat                 # start chatting; /exit to quit, /reset to clear context
uv run chimera chat --fuse          # fuse deep-reasoning turns
uv run chimera chat --no-memory     # don't recall long-term memory
```

Derselbe Konversationskern treibt das TUI und das (kommende) Messaging-Gateway
an.

### `tui` — Vollbild-Terminal-App

Eine Textual-Vollbild-UI über demselben Konversationskern. Zwei Bereiche: ein
**Gesprächsprotokoll**, das Antworten als Markdown rendert (eingezäunter Code
wird syntaxhervorgehoben), wobei die Token des Modells **live beim Eintreffen
gestreamt** werden; und ein **Aktivitätspanel**, das zeigt, was der Agent in
diesem Turn getan hat — die aufgerufenen Tools, Tokenanzahl und Kosten, und
wie viele Gedächtnisfakten abgerufen wurden. Dieselben Flags wie `chat`.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
```

Befehle: `/model <slug>` · `/reset` (Kontext leeren) · `/clear` (Bildschirm
leeren) · `/stream` (Live-Token umschalten) · `/help` · `/exit`. Tasten:
`Ctrl+R` zurücksetzen · `Ctrl+L` leeren · `Ctrl+P` Befehlspalette ·
`PgUp`/`PgDn` scrollen · `Ctrl+C` beenden. Slash-Befehle werden beim Tippen
automatisch vervollständigt.

Ehrlichkeitshinweise: Token-Streaming ist nur der Single-Model-Pfad — unter
`--fuse` (ein Panel-→Judge-→Synthesizer-Turn) gibt es keine inkrementellen
Token, daher zeigt das Panel einen "synthesizing"-Status statt eines
vorgetäuschten Cursors. Die Kosten zeigen "unavailable", wenn der Listenpreis
des Modells unbekannt ist (nie geraten). Es gibt hier keine Verify-/Revert-
Anzeige: verify-or-revert läuft in `solve`/`project`, nicht im Chat. Ist
Textual nicht installiert, fällt `tui` auf das einfache `chat`-REPL zurück.

### `serve` — Messaging-Gateway (HTTP oder Discord)

Exponiert den Agenten mit einem Gespräch (und dessen Gedächtnis) **pro Chat**.
Der Routing-Kern ist transportagnostisch; Adapter klinken sich ein.

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Jede `chat_id` behält ihren eigenen Kontext, sodass sich verschiedene
Nutzer/Threads nicht vermischen.

**Unbeaufsichtigter Betrieb (Webhooks).** Einen Job registrieren, der auf einen
eingehenden HTTP-POST feuert, sodass Chimera läuft, ohne dass jemand tippt —
ein GitHub-Push, ein Stripe-Event, ein Cron-as-a-Service-Ping:

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

Der POST-Body wird der Aufgabe des Jobs als Kontext übergeben, und jeder für
diesen Hook registrierte Job läuft. `GET /health` und `POST /chat`
funktionieren daneben weiter.

**Natives Discord.** Chimera als Discord-Bot laufen lassen — jeder Kanal ist
eine Sitzung, und der Agent kann auch über das `send_message`-Tool Nachrichten
senden:

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

Den Bot unter <https://discord.com/developers> anlegen, den **Message
Content**-Intent aktivieren und ihn auf den eigenen Server einladen. Er
antwortet in jedem Kanal, den er sehen kann (gefiltert, um eigene und andere
Bot-Nachrichten zu ignorieren). Der Token wird aus der Umgebung gelesen — nie
hartcodiert.

**Natives Telegram.** Dasselbe Adaptermuster, und es braucht **keine
zusätzliche Abhängigkeit** (die Telegram-Bot-API ist reines HTTP):

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**Natives Slack.** Empfängt über Socket Mode (braucht das `messaging`-Extra)
und sendet über die Web-API. Socket Mode in der eigenen Slack-App aktivieren,
um einen App-Level-Token zu erhalten:

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp (senden).** WhatsApp ist *push-basiert* (Nachrichten kommen an
einem selbst gehosteten Meta-Webhook an), daher gibt es anders als bei den
anderen keine Verbindung zu öffnen. Die Cloud-API-Zugangsdaten setzen, und der
Agent kann über das `send_message`-Tool in jedem `serve`-Modus WhatsApp-
Nachrichten **senden**:

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**Zweiseitiges WhatsApp.** Den Webhook der eigenen Meta-App auf
`https://<your-host>/whatsapp` richten und `CHIMERA_WHATSAPP_VERIFY_TOKEN`
setzen (ein beliebiger, selbst gewählter String, der zur App-Konfiguration
passt). `chimera serve` verifiziert dann das Abonnement (`GET /whatsapp`) und
leitet eingehende Nachrichten (`POST /whatsapp`) durch das Gateway, antwortet
über die Cloud-API. WhatsApp braucht weiterhin eine öffentliche URL für den
Webhook — das ist der einzige Teil außerhalb von Chimera.

**Natives Signal (zweiseitig).** Signal hat keine offizielle API, daher
spricht Chimera mit einer selbst betriebenen
[`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api)-
Bridge (Docker), die mit der eigenen Nummer verknüpft wird — reines HTTP,
keine Python-Abhängigkeit:

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — Tier-1, Single-Shot-Completion

Ein einzelner Modellaufruf, keine Tools, keine Fusion. Günstigster Weg.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**Vision / Bild einfügen.** Bilder mit `--image` anhängen (ein Pfad oder eine
URL, wiederholbar) — braucht ein vision-fähiges Modell:

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Deliverable Mode (ein Artefakt erzeugen)

Wo `run`/`chat` gesprächsweise antworten, erzeugt `deliver` ein vollständiges,
in sich abgeschlossenes Dokument (Bericht, Plan, Spezifikation, README …) und
schreibt es in eine Datei.

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — die rohe ReAct-Tool-Calling-Loop

Gedanke → Aktion (Tool) → Beobachtung, bis zu einer finalen Antwort. Tools sind
auf den Workspace beschränkt.

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion (das Alleinstellungsmerkmal)

Führt ein *Panel* von Modellen aus, ein *Judge* analysiert deren Antworten
(Konsens / Widersprüche / blinde Flecken), und ein *Synthesizer* schreibt die
finale Antwort. `--show-panel` nutzen, um die vollständige Spur zu sehen.

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

Fusion kostet etwa das 2- bis 3-Fache eines einzelnen Aufrufs, daher sie für
schwieriges Reasoning reservieren. `fuse` gibt auch die Token-Kosten pro Stufe
(Panel / Judge / Synth) aus, sodass sichtbar ist, wohin die Token eines Laufs
tatsächlich fließen.

**Selektive Fusion (standardmäßig AN, spart Token).** Die Engine prüft die
ersten `CHIMERA_FUSION_PROBE_K` Panel-Modelle (Standard 2) und überspringt,
wenn deren Antworten eng übereinstimmen, den Rest des Panels *und* den Judge
— synthetisiert direkt aus den übereinstimmenden Antworten. Der
Übereinstimmungscheck ist ein günstiger lokaler Textvergleich (kein
zusätzlicher Modellaufruf), sodass ein *abweichender* Turn zur vollen Pipeline
eskaliert und genau so viel kostet wie volle Fusion, während ein
*übereinstimmender* Turn günstiger ist. Die Schwelle mit
`CHIMERA_FUSION_AGREEMENT` (0–1, Standard 0,8) einstellen, oder
`CHIMERA_FUSION_MODE=full` setzen (oder `--full` übergeben), um immer das
gesamte Panel + den Judge laufen zu lassen.

Warum es der Standard ist: über 3 Läufe von `chimera fusion-bench --tasks
hard` (ein bezahltes 3-Modell-Panel) senkte es die Token um **~20–28 %** und
lag bei **jedem** Turn richtig, bei dem tatsächlich abgekürzt wurde (16/16).
Die Gesamtgenauigkeit schwankte zwischen den Läufen um 0 bis −8,3
Prozentpunkte, aber diese Varianz landet vollständig im *eskalierten* Bucket
— wo selektiv dieselbe Pipeline wie voll ausführt —, es ist also
Modell-Nichtdeterminismus, keine Kosten des frühen Stoppens. Den Bench auf der
eigenen Workload ausführen, um den Trade-off für das eigene Panel und die
eigenen Aufgaben zu sehen:

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **Zuverlässige Panel-Modelle wählen.** Fusion zahlt sich nur aus, wenn jedes
> Panel-Mitglied tatsächlich antwortet. OpenRouter-`:free`-Modell-Slugs in
> `CHIMERA_FUSION_PANEL` vermeiden — sie werden unter echter Last ratenlimitiert
> (HTTP 429), und das Panel schrumpft still auf das übrige bezahlte Modell. Ein
> günstiges, zuverlässiges Trio: `openrouter/deepseek/deepseek-chat`,
> `openrouter/openai/gpt-4o-mini`, `openrouter/meta-llama/llama-3.3-70b-instruct`.

### Skill-Cards (TRS-Reasoning-Cards, experimentell)

Der Agent destilliert, was er lernt, in **Reasoning-Cards** — die fünf Felder
Trigger / Do / Avoid / Check / Risk (plus Retrieval-Schlüsselwörter) — sowohl
aus Erfolgen (eine *Pattern*-Card) als auch aus wiederkehrenden Fehlschlägen
(eine beratende *Anti-Pattern*-Card). Bei `CHIMERA_SKILL_CARDS=on` ruft
`solve` die top-k relevanten Cards ab (BM25 über Name + Beschreibung +
Trigger) und injiziert sie in den Reasoning-Kontext des Workers, sodass der
Agent wiederverwendet, was funktioniert hat, und bekannte Fehlermodi
vermeidet. Das schließt den Kreis — vorher wurden gelernte Skills gespeichert
und nie zurückgelesen.

Standardmäßig aus: Das Injizieren von Cards fügt Prompt-Token hinzu, und die
*Token*-Einsparungen von TRS kommen aus dem Verkürzen langer Reasoning-Spuren,
sodass bei kurzen Antwortaufgaben der Vorteil in der Genauigkeit liegt, nicht
in den Kosten. Das ist nicht hypothetisch — auf der `hard`-Kurzantwort-Suite
(bezahltes deepseek-v3.1) maß `skillcard-bench` Cards mit **+290 % Token** und
**−8 Prozentpunkten Genauigkeit** gegenüber keinen Cards: bei einem nahezu an
der Obergrenze liegenden Modell und ohne lange Spur zum Kürzen sind generische
Cards purer Overhead, der ablenken kann. Cards für **Long-Reasoning**-
Workloads aktivieren (Mathe/Coding mit langen Spuren), wo die Token-Rechnung
kippt, und immer zuerst den eigenen Trade-off mit einer Ground-Truth-Prüfung
messen:

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

Der Bench meldet die Genauigkeit mit vs. ohne Cards, das Token-Delta, die
Card-Trefferquote und die Genauigkeit aufgeteilt nach Treffer/Fehltreffer, mit
einem PASS-Urteil, wenn die Card-Genauigkeit innerhalb von 1 Prozentpunkt der
Baseline ohne Cards bleibt.

### Kompakte Tool-Schemas (experimentell)

Tool-Schemas — insbesondere solche, die aus MCP-Servern oder OpenAPI-Specs
importiert wurden — tragen Annotationsrauschen (Beispiele, Titel, Defaults,
mehrsätzige Parameterprosa, verschachtelte Request-Bodys), das bei **jedem**
ReAct-Schritt erneut an das Modell gesendet wird. Mit
`CHIMERA_COMPACT_SCHEMAS=on` wird dieses Rauschen entfernt und
Parameterbeschreibungen zum Zeitpunkt der Ankündigung gekürzt, **ohne**
irgendetwas anzurühren, das einen Aufruf beeinflusst (der Funktionsname und
die Beschreibung sowie `type` / `properties` / `required` / `enum` jedes
Schemas bleiben erhalten). Die kanonischen Schemas bleiben unangetastet — nur
die an das Modell gesendete Kopie schrumpft.

Die Ersparnis ist bei ausführlichen MCP-/OpenAPI-Toolsets am größten und
summiert sich über jeden Schritt; native Tools sind bereits knapp, ihre
Reduktion also gering. Zuerst das eigene Toolset messen (keine
Modellaufrufe — es zählt nur Token):

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

Standardmäßig aus. Da die Kompaktierung nur Annotationsrauschen entfernt
(nie Struktur), ist das einzige Risiko, dass das Modell etwas weniger Prosa
hat, um ein Tool auszuwählen — daher bleibt es konservativ, und man sollte
das Tool-Aufrufverhalten auf der eigenen Workload bestätigen, bevor man es
aktiviert.

### `solve` — Tier-2 autonom (Plan + verify-or-revert)

Plant die Aufgabe, führt sie mit der Agent-Loop aus und **verifiziert dann mit
einem ausführbaren Befehl**. Schlägt die Verifikation fehl, wird der Workspace
zurückgesetzt und mit Feedback erneut versucht. Der Verifier (Exit-Code 0 =
Erfolg) ist Ground Truth.

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

Nützliche Flags:

| Flag | Bedeutung |
|------|---------|
| `--verify "<cmd>"` | Befehl, der mit 0 enden muss (Tests, ein Build, ein Linter) |
| `--workspace`, `-w` | wo der Agent liest/schreibt (Standard `.`) |
| `--max-attempts N` | verify-or-revert-Budget (Standard 3) |
| `--max-steps N` | Tool-Calling-Schritte pro Versuch (Standard 8) |
| `--fuse` | den **Plan** per Fusion erzeugen (tiefes Reasoning) |
| `--guard` | jeden Tool-Aufruf durch den Governance-Kernel leiten |
| `--no-plan` / `--no-manager` | die Planungs-/Review-Stufe überspringen |
| `--rubric` | der Manager urteilt über die **Cascade-Rubrik** (Instruction-Following → Faktentreue → Rationalität) |
| `--no-remember` | bei Erfolg keinen Gedächtnisfakt automatisch schreiben |
| `--no-evolve-skills` | bei wiederkehrender Aufgabe keinen gelernten Skill automatisch vorschlagen |
| `--isolate` | in einem Wegwerf-Git-Worktree laufen; geänderte Dateien werden nur bei Erfolg zurückkopiert |
| `--require-diff` | ein Versuch, der **keine Datei** geändert hat, schlägt fehl und wird erneut versucht — bei einer Code-Aufgabe ist eine Erklärung kein Fix |
| `--keep-workspace` | bei Fehlschlag die Bearbeitungen des letzten Versuchs auf der Platte lassen, statt zurückzusetzen — für wenn ein **externer** Bewerter über bestanden/nicht bestanden entscheidet |
| `--diff-feedback` | einem fehlgeschlagenen Versuch seinen eigenen zurückgesetzten Diff zeigen, gerahmt als Weg, der nicht erneut gegangen werden soll |
| `--stagnation-fuzzy` | wiederholte Fehlschlagssignaturen näherungsweise abgleichen, damit der Anti-Stagnations-Kurswechsel bei Fehlschlägen mit gleicher Ursache, aber unterschiedlicher Formulierung auslöst |

> **Zu `--max-steps`.** Der Standard von 8 ist auf kleine Workspaces
> abgestimmt. Bei einem **großen Repository ist das die bindende
> Einschränkung**, nicht das Modell: SWE-bench-Lauf 1 erreichte eine exakte
> 0,0 Prozentpunkte bei 8 Schritten gegen ein 250-MB-Checkout, und dieselbe
> Konfiguration bei **30 Schritten** hob die Patch-Rate der Baseline von 47 %
> auf 74 % ([`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)).
> Wenn der Agent erkundet und dann ohne Bearbeitung fertig wird, das zuerst
> erhöhen.

> **`--require-diff` und `--keep-workspace` sind für externe Bewertung.**
> `solve` ist verify-or-revert: Wenn *es* die Bestanden/nicht-bestanden-
> Entscheidung trägt, ist das Zurücksetzen eines fehlgeschlagenen Versuchs
> richtig. Trägt etwas anderes sie — ein CI-Job, ein Benchmark-Harness, ein
> Mensch, der den Diff prüft —, verhindert `--keep-workspace`, dass die
> Arbeit des Agenten zurückgerollt wird, bevor dieser Richter sie je sieht,
> und `--require-diff` verhindert, dass eine selbstbewusste Erklärung als
> abgeschlossene Änderung bewertet wird. Beide sind **standardmäßig aus**.

**`solve` lernt über Läufe hinweg.** Jeder Lauf speist eine geschlossene
Verhaltensschleife, alle durch verify-or-revert abgesichert, sodass nur
verifizierte Arbeit eine Wirkung hat: (1) relevante **Lektionen** aus
vergangenen Versuchen (Fehlschläge werden bevorzugt) werden in den Plan/Prompt
eingearbeitet, und der **erste fehlerhafte Schritt** eines fehlgeschlagenen
Versuchs wird lokalisiert und in den Retry eingespeist; (2) bei verifiziertem
Erfolg wird ein deduplizierter **Gedächtnis**-Fakt geschrieben (später von
`chat`/`crew` abgerufen); und (3) wenn sich ein Aufgabenmuster wiederholt (≥ 2
vorherige Erfolge), wird ein wiederverwendbarer **Skill** vorgeschlagen — über
das Fusion-Panel hinweg und behalten durch modellübergreifende
**Übertragbarkeit**, wenn `--fuse` aktiv ist — und nur behalten, wenn er die
Governance-Validierung und einen ausführbaren Smoke-Test besteht.

### `crew` — Tier-3 Multi-Agent

Ein Team von Rollenagenten arbeitet an einer Aufgabe zusammen, und ein
Supervisor synthetisiert die finale Antwort.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — SDLC-Crew (plan → build → test → review)

Eine vorgefertigte Software-Lifecycle-Pipeline mit **verify-or-revert** in der
Test-Stufe: `plan` zerlegt die Aufgabe, `build` implementiert sie, `test`
führt den Verifier aus (setzt bei Fehlschlag zurück und versucht den Build
erneut), und ein Reviewer kritisiert das Ergebnis.

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

Jede Stufe wird mit ✓/✗ ausgegeben; der Lauf ist nur dann `success`, wenn der
Verifier der Test-Stufe bestanden hat.

### `meta` — Agenten bauen Agenten

Entwirft einen spezialisierten Agenten-Bauplan (Name, Tools, Rollen-Prompt) für
eine Aufgabe.

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — Governance-Urteil

Zeigt die Entscheidung des Trust-Kernels (allow / warn / review / block) für
eine Aktion.

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — Benchmark für kontinuierliche Evolution

Misst, ob die Leistung über eine Kette von Aufgaben hinweg *hält* (der Beweis
gegen Degradation): Gesamt-Erfolgsquote, erste vs. zweite Hälfte, längste
Serie.

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

Der Bericht trägt auch ein **statistisch ehrliches** Degradations-Flag: statt
einer bloßen Subtraktion erste-minus-zweite-Hälfte zu vertrauen (bei einer
kurzen Kette ist ein Ausschlag von 0,2 meist Rauschen), ist
`degraded_significant` nur `1.0`, wenn ein Wilson-Konfidenzintervall auf den
Rückgang null ausschließt, `-1.0`, wenn die Stichprobe zu klein für eine
Aussage ist, und sonst `0.0` — plus die Grenzen `degradation_ci_low/high`.
Separat davon gattert `CHIMERA_SKILL_ACCEPT_MODE=wilson` die
modellübergreifende Skill-Akzeptanzentscheidung an der *unteren*
Konfidenzgrenze der Transferrate (sodass ein glücklicher 2-von-3-Erfolg nicht
mehr zählt); der Standard `point` behält die rohe Rate, da die Wilson-Grenze
bei winzigen Panels streng ist.

### `sandbox-bench` — Bewertung von Zustand + Nebenwirkungen

Die Text-Benches bewerten die *Antwort* des Modells; dieser hier bewertet, was
der Agent **getan** hat. Jede Aufgabe läuft in einem isolierten Sandbox-
Verzeichnis, und der Harness vergleicht den finalen Dateizustand mit dem Ziel
(jeder Pfad erlaubt, ergebnisorientiert) **und** zählt separat *schädliche
Nebenwirkungen* — Mutationen außerhalb der für die Aufgabe erklärten erlaubten
Menge. So wird ein Agent, der das richtige Ergebnis liefert, dabei aber eine
nicht zusammenhängende Datei zerstört, erwischt, statt als sauberer Erfolg
bewertet zu werden.

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

Meldet `pass_rate` und `side_effect_rate`. Es liefert die *Methodik* (eine
`StatefulTask` mit `goal_check` + erlaubter Mutationsmenge), keine große
Aufgabensuite — eigene Aufgaben für die eigenen Tools verfassen. Die
bestehenden Text-Grader bleiben für reine Q&A-Arbeit korrekt.

### `memory` — kuratiertes Langzeitgedächtnis

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

Der Recall durchläuft ein **Zulassungs-Gate** (eine Vertrauensgrenze): eine
abgerufene Erinnerung gelangt nur dann in den Prompt, wenn sie relevant *und*
frei von Override-/Injection-Text ist (Verteidigung gegen gedächtnisbasierten
Jailbreak). `memory prune` vergisst innerhalb eines Budgets nach einem
mehrfaktoriellen **Wert**-Modell (Aktualität, Spezifität, Art, Kuratierung,
Zuverlässigkeit) — nicht nach einem einzigen Hinweis.

Die **Graph-Schicht** extrahiert `(Quelle, Relation, Ziel)`-Tripel aus den
Erinnerungen (`PassaPro uses Supabase`, `Alex prefers TypeScript`), sodass
Fakten nach Entität abgerufen werden können, nicht nur nach Schlüsselwort.

### `cron` — geplante Jobs & Event-SOPs

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — Aufgabenboard mit Worker-Bahnen

Ein Board (`backlog → doing → review → done`), bei dem jede Karte eine *Bahn*
benennt, die sie an den Agenten-Stack weiterleitet: `solve` (Tier-2 autonom,
verify-or-revert) oder `crew` (Tier-3-Rollen-Pipeline). Die operative Ansicht
der Schleife, die der Agent bereits ausführt.

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` bewegt jede Karte von backlog → doing → done (Erfolg) oder → review
(braucht Aufmerksamkeit). `learn` nutzt den Wiederkehr-Detektor des Cron-
Learners wieder, um Aufgaben, die der Agent wiederholt, in die Warteschlange
zu stellen (dedupliziert gegen das Board) — zeitgesteuert einplanen, um das
Backlog automatisch zu füllen.

### `workflow` — entworfene Schleifen (Loop Engineering)

Eine autonome Schleife als YAML statt als Ad-hoc-Prompt verfassen. Jeder
Schritt `uses` eine Fähigkeit (`run` / `shell` / `solve` / `crew` /
`lifecycle`), kann an den vorherigen Schritt gebunden werden (`when:
prev_succeeded | prev_failed`) und kann sich wiederholen (`repeat`, `until:
success`).

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

### `drift` — Spec↔Code-Drift-Gate

Spec und Code aneinander ausgerichtet halten. Eine Spec ist ein kleines YAML
von Anforderungen (`defines` ein Symbol / `contains` ein Regex / `absent` ein
Regex / `command` endet mit 0). Das Gate beendet sich bei Drift ungleich null,
dient also gleichzeitig als Verifier.

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — Import von einem anderen Agenten

Bringt **Konfiguration + Skills** von Hermes oder OpenClaw mit, und mit
`--apply` **merged** es auch das Langzeitgedächtnis (dedupliziert, nicht
destruktiv). Standard ist eine Trockenlauf-Vorschau.

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

Der Gedächtnis-Merge meldet die Zählungen `{ADD, UPDATE, NOOP}` — Duplikate
werden zu `NOOP`, ein erneuter Lauf ist also sicher.

### `evolve` — opt-in Modell-Evolution (fortgeschritten)

`chimera solve --collect` (standardmäßig an) protokolliert jeden Lauf als
Trajectory. Die `evolve`-Befehle verwandeln diese in trainingsbereite
Datensätze und ein lauffähiges LoRA-Rezept. **Das Training ist extern und
opt-in** — es ändert Modellgewichte, geschieht also nie automatisch; Chimera
bereitet die Daten und ein Skript vor und hört dann auf.

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` akzeptiert Rezept-Stellschrauben: `--min-steps N` behält nur
langfristige Spuren, `--diverse` behält höchstens ein Beispiel pro Aufgabe
(Aufgabenvielfalt ist der Kuratierungs-Engpass), und `--min-process P`
(SkillCoach) behält nur Spuren, deren *Schritt-Folgetreue*-Score ≥ P ist —
der Anteil der Tool-Schritte, die ein erfolgreiches, sichtbares Ergebnis
lieferten —, sodass ein Glückstreffer, der sich durch fehlgeschlagene Tool-
Aufrufe wühlte, nicht trainiert wird. Die Pro-Schritt-Events hinter diesem
Score werden bei jedem `solve`-Lauf automatisch erfasst; der Filter ist
standardmäßig aus (`CHIMERA_SFT_MIN_PROCESS` setzt einen globalen Standard).
`evolve tune` unterscheidet sich vom Training — es führt eine **Meta-Suche**
über die Agenten-*Spec* aus (Modell, System-Prompt, Schrittbudget, Panel,
Gedächtnistiefe), bewertet jeden Kandidaten anhand der täglichen Szenarien
und behält eine Änderung nur bei **Nicht-Regression**. Es ruft Modelle auf,
ändert aber nie Gewichte, ist also jederzeit sicher auszuführen.

Dann, um tatsächlich zu trainieren, auf einer GPU (oder Colab):
`pip install chimera-agent[train]` (oder die `requirements.txt` des Rezepts)
und `python recipe/train.py`. `CHIMERA_DEFAULT_MODEL` beim Serving auf das
Basismodell + den Adapter richten.

### `pet` — ein virtueller Begleiter

Ein dauerhafter kleiner Begleiter, dessen Werte driften, während man
abwesend ist. Kein Key nötig.

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## Tipps

- **Tools vs. Reasoning.** Tool-Calling-Turns nutzen immer ein einzelnes
  Modell (Fusion kann keine Tools aufrufen); Fusion ist für werkzeugloses
  tiefes Reasoning reserviert.
- **Nachvollziehen, was passiert ist.** `CHIMERA_LOG_LEVEL=DEBUG` bringt
  Routing- und Fusion-Engagement-Logs zum Vorschein.
- **Tests ehrlich halten.** Ein guter `--verify`-Befehl (eine echte
  Testsuite) macht `solve` zuverlässig — er ist die ausführbare Ground Truth,
  an der der Agent gemessen wird.
