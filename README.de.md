<div align="center">

<img src="assets/logo-wide.png" alt="Chimera logo" width="460" />

# Chimera

**Der kontrollierte, sich selbst weiterentwickelnde Agent — bewiesen und kontrolliert.**<br/>
<sub>Denkt mit vielen Köpfen, erledigt die Arbeit selbst, lernt nur Bewiesenes und ist sicher durch Architektur.</sub>

[![PyPI](https://img.shields.io/pypi/v/chimera-agent.svg?color=blue&label=PyPI)](https://pypi.org/project/chimera-agent/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-beitreten-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
[![Reddit](https://img.shields.io/badge/Reddit-r%2FChimeraAgent-FF4500.svg?logo=reddit&logoColor=white)](https://www.reddit.com/r/ChimeraAgent/)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![Donate](https://img.shields.io/badge/Donate-Stripe-635BFF.svg?logo=stripe&logoColor=white)](https://donate.stripe.com/9B63cofM491m4SBfe177O00)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <b>Deutsch</b> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

Die meisten KI-Assistenten setzen alles auf ein **einziges** Modell und vergessen alles, sobald der
Chat endet. **Chimera macht zwei Dinge anders:** Bei schweren Fragen fragt es **mehrere** KI-Modelle
gleichzeitig und verschmilzt ihre Antworten zu einem stärkeren Ergebnis, und es **merkt sich Dinge
und lernt**, sodass es umso nützlicher wird, je öfter du es benutzt. Es plaudert nicht nur — gib ihm
ein Ziel, und es plant, nutzt Werkzeuge, überprüft seine eigene Arbeit und behält nur das, was
wirklich funktioniert.

> **Kostenlos und quelloffen (Apache-2.0), in früher, aber aktiver Entwicklung.** Es funktioniert
> bereits von Anfang bis Ende: chatte mit ihm, lass es Aufgaben eigenständig erledigen, betreibe es
> als Bot in deiner Lieblings-Messaging-App, stelle es auf einem Server bereit, damit es rund um die
> Uhr arbeitet, und beobachte, wie es aus seinem Tun lernt. Es ist **Alpha** — solide und ausgiebig
> getestet (1000+ automatisierte Tests, strikte Typprüfung und Linting bei jeder Änderung), aber im
> Produktivbetrieb noch nicht kampferprobt.

---

## Warum Chimera

Stell dir die meisten KI-Werkzeuge so vor, dass du **einen** Experten fragst und hoffst, dass er
recht hat. Chimera ist wie ein **Gremium aus Experten**, das debattiert, ein **fairer Richter**, der
ihre Antworten abwägt, und ein **Autor**, der das beste kombinierte Ergebnis liefert — und dann ein
Teamkollege, der die Arbeit tatsächlich **erledigt** und daraus **lernt**. Was es besonders macht, in
einfachen Worten:

- 🧠 **Viele Köpfe, eine Antwort.** Bei kniffligen Fragen stellt Chimera mehreren Modellen dieselbe Frage, lässt ein Modell ihre Antworten vergleichen und lässt ein finales Modell die beste kombinierte Antwort schreiben — so bekommst du etwas Ausgewogeneres, das seltener falsch liegt als ein einzelnes Modell für sich. (Es tut das nur, wenn es sich lohnt, um schnell und günstig zu bleiben.)
- 🚀 **Es macht die Arbeit, nicht nur Gerede.** Gib ihm ein Ziel. Es zerlegt es, nutzt Werkzeuge, bearbeitet Dateien, führt die Tests aus und **behält eine Änderung nur, wenn sie besteht**. Geht etwas kaputt, macht es die Änderung rückgängig und versucht es erneut — so hinterlässt es kein Chaos.
- 🧬 **Es wird besser, je mehr du es benutzt.** Es merkt sich deine Vorlieben und wichtige Fakten über Gespräche hinweg und verwandelt Aufgaben, die es wiederholt, still und leise in wiederverwendbare Fähigkeiten. Es ist darauf ausgelegt, sich stetig zu verbessern, statt über lange Läufe langsam schlechter zu werden — ein Problem, das viele Agenten unbemerkt aushöhlt.
- 🛡️ **Sicher von Grund auf.** Jede riskante Aktion durchläuft zuerst eine Sicherheitsprüfung, alles Zerstörerische fragt nach Bestätigung, und es kann nicht vertrauenswürdigen Code in einer abgeschotteten Sandbox ausführen. (Diese Prüfungen sind ein günstiger erster Filter, nicht die eigentliche Grenze — die Sandbox ist es; und die Container-Isolierung ist optional. Siehe [SECURITY.md](SECURITY.md).)
- 🔌 **Jedes Modell, läuft überall.** Nutze große gehostete Modelle oder deine eigenen lokalen über eine einzige Schnittstelle — auf deinem Laptop oder einem 5-Dollar-Server, rund um die Uhr.
- 🧩 **Wirklich deins.** Quelloffen, kein Lock-in, kein Anbieter-Konto nötig. Du betreibst es, es gehört dir, du kannst alles ändern.

## Funktionen

### 🧠 Denken & Handeln
- **Mehrere Modelle zu einer Antwort verschmelzen** (`chimera fuse`) — ein Gremium aus Modellen, ein Richter, der aufzeigt, wo sie übereinstimmen, sich widersprechen oder etwas übersehen, und ein Synthesizer, der die finale Antwort schreibt. Ein smarter Router investiert diesen zusätzlichen Aufwand nur bei schweren Problemen, und wenn sich die ersten Modelle bereits einig sind, bricht er frühzeitig ab — in unseren Benchmarks gemessen mit ~20–28 % weniger Tokens ohne Genauigkeitsverlust. (Fusion / Mixture-of-Agents an sich ist nichts Einzigartiges — es gibt sie in OpenRouter und anderen Tools; der Unterschied hier ist, dass sie in die Agenten-Schleife hinter diesem kostenbewussten Router eingebaut und gemessen ist, kein Modell, das man auswählt.)
- **Aufgaben eigenständig erledigen** (`chimera solve`) — es plant, handelt mit Werkzeugen und **verifiziert dann und macht rückgängig**: Es führt deine Prüfung aus (z. B. Tests) und behält die Änderung nur, wenn sie besteht, andernfalls macht es sie rückgängig und versucht es erneut. Optional arbeitet es an einer isolierten Kopie deines Projekts, sodass nichts angefasst wird, bis es bewiesen ist.
- **Teams von Spezialisten** (`chimera crew`, `chimera crew-isolated`) — mehrere rollenfokussierte Agenten teilen sich eine Aufgabe. Im isolierten Modus arbeitet jeder an seiner **eigenen privaten Kopie parallel**; sichere Änderungen werden zusammengeführt, Konflikte werden gemeldet statt still überschrieben, und die Änderungen eines schlechten Workers können durch einen Test pro Worker abgelehnt werden. Ein Supervisor kann die Arbeit aller zu einem einheitlichen Bericht zusammenfügen.
- **Delegieren und erkunden** — jeder Agent kann eine in sich geschlossene Teilaufgabe an einen frischen **Subagenten** übergeben, der nur das Ergebnis zurückmeldet, sodass der Hauptkontext sauber bleibt. Der **Context Explorer** (`chimera explore`) findet die richtigen Dateien und Zeilen in einer Codebasis und liefert eine kurze Antwort, statt alles abzuladen.

### 🧬 Gedächtnis & Selbstverbesserung
- **Langzeitgedächtnis** — es behält Kurzzeit-, jüngste, faktische und Über-dich-Erinnerungen, plus eine Karte, wie Dinge zusammenhängen. Es kann Erinnerungen in einer schnellen Volltext-Datenbank speichern, ein Profil deiner Vorlieben in jeden Chat mitnehmen, doppelte Notizen automatisch zusammenführen und behutsam vorschlagen, eine Vorliebe zu speichern, wenn du eine erwähnst.
- **Lernt neue Fähigkeiten** — wenn es bei derselben Art von Aufgabe mehr als einmal erfolgreich ist, verwandelt es das automatisch in eine getestete, wiederverwendbare Fähigkeit.
- **Optionales Selbsttraining (fortgeschritten)** — es kann seine eigene Erfahrung aufzeichnen, damit du später ein Modell daraus feinjustieren kannst. Standardmäßig aus; nichts wird trainiert, ohne dass du danach fragst.

### 🔌 Verbinden & Automatisieren
- **Sprich überall mit ihm** — ein Terminal-Chat, eine Vollbild-Terminal-App oder als Bot auf **Discord, Telegram, Slack, Signal und WhatsApp**. Es gibt außerdem einen einfachen HTTP-Endpunkt.
- **Zeitplanung & Proaktivität** — gib ihm wiederkehrende Aufgaben in einfacher Sprache ("fasse jeden Morgen die Nachrichten zusammen"). Mit dem eingebauten Scheduler in Betrieb **handelt es pünktlich**, nicht nur, wenn du ihm schreibst.
- **Werkzeuge & Integrationen** — Dateien lesen und schreiben, Shell-Befehle ausführen, im Web browsen und Code sicher in einer Sandbox ausführen. Verbinde nahezu jeden Webdienst (über seine API) oder ein externes Werkzeug und importiere deine Einrichtung aus anderen Agenten-Werkzeugen, die du bereits nutzt.
- **Alles inklusive** — Websuche, Bilderzeugung, Text-to-Speech, E-Mail, Kalender, Code-Ausführung und mehr, bereit zum Einschalten.

### 🚀 Überall laufen, sicher
- **Jedes Modell, eine Schnittstelle** — gehostete Modelle oder deine eigenen lokalen, mit automatischem Fallback, falls eines ausfällt, und Rotation über mehrere Schlüssel.
- **Server-Deployment mit einem Befehl** — betreibe es mit Docker (oder auf Bare-Metal), sodass es läuft und beim Neustart wieder hochfährt. Siehe **[docs/deploy.md](docs/deploy.md)**.
- **Sicherheitskern** — eine Prüfung bei jeder Aktion (erlauben / warnen / blockieren / nachfragen), ein **optionaler** netzwerkisolierter Container für nicht vertrauenswürdigen Code (`CHIMERA_SANDBOX=docker`; der Standard-Runner *local* ist *nicht* isoliert) und ein vollständiges Audit-Protokoll dessen, was es getan hat.

## Schnellstart

Du brauchst **Python 3.11+** und [uv](https://docs.astral.sh/uv/) (einen schnellen Python-Installer).

**1. Installieren** — von PyPI:
```bash
pip install chimera-agent
```
Damit steht der Befehl `chimera` bereit. (Die Beispiele unten nutzen `uv run chimera` für einen
Checkout aus dem Quellcode — mit pip install genügt `chimera …`.) Um an Chimera selbst zu arbeiten, klone das Repo:
```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev
```

**2. Einen KI-Anbieter-Schlüssel hinzufügen.** Am einfachsten ist ein [OpenRouter](https://openrouter.ai)-
Schlüssel — ein Schlüssel schaltet über 100 Modelle frei.
```bash
cp .env.example .env
# .env öffnen und z. B. setzen:  CHIMERA_OPENROUTER_KEYS=sk-or-...
```

**3. Prüfen, ob alles bereit ist**
```bash
uv run chimera doctor
```

**4. Ausprobieren**
```bash
uv run chimera chat                         # ein Gespräch führen (es merkt sich Dinge)
uv run chimera run "Explain what you can do in 3 bullets"
uv run chimera fuse "What's the best way to learn to cook?" --show-panel   # mehrere Modelle verschmolzen sehen
uv run chimera solve "add a hello() function to app.py and a test for it" --verify "pytest -q"
```

**Auf einem Server betreiben (damit es rund um die Uhr arbeitet):**
```bash
docker compose up -d      # Gateway + Scheduler; startet automatisch neu
```
Vollständige Anleitung (Docker oder systemd, Zeitplanung, Backups, Sicherheit): **[docs/deploy.md](docs/deploy.md)**.

## Wie es funktioniert

Gib Chimera eine Aufgabe; es plant, denkt (verschmilzt Modelle, wenn das Problem schwer ist), handelt
mit Werkzeugen, **überprüft seine eigene Arbeit und behält nur, was besteht**, und lernt dann aus dem
Ergebnis — indem es Gedächtnis und neue Fähigkeiten in die nächste Aufgabe zurückspeist.

```mermaid
flowchart TD
    U([Du: eine Aufgabe oder Frage]) --> P[Verstehen & planen]
    P --> Q{Ist es ein schweres Problem?}
    Q -- ja --> FUSION[Mehrere Modelle fragen<br/>· ein Richter vergleicht sie<br/>· ein Synthesizer schreibt die beste Antwort]
    Q -- nein --> ONE[Ein schnelles Modell nutzen]
    FUSION --> ACT[Handeln: Werkzeuge, Dateien, das Web<br/>oder an Subagenten delegieren]
    ONE --> ACT
    ACT --> V{Hat es funktioniert?<br/>Tests / Prüfungen ausführen}
    V -- ja --> KEEP[Änderung behalten]
    V -- nein --> REVERT[Rückgängig & mit der Lektion erneut versuchen]
    REVERT --> ACT
    KEEP --> LEARN[Lernen: Wichtiges ins Gedächtnis speichern,<br/>wiederholte Arbeit in eine Fähigkeit verwandeln]
    LEARN --> U
    MEM[(Langzeitgedächtnis)] -. erinnert .-> P
    LEARN -. schreibt .-> MEM
    GOV[[Sicherheitsprüfung bei jeder Aktion]] -. schützt .-> ACT
```

## Befehle

Jeder Befehl lautet `chimera <name>` (oder `uv run chimera <name>` vor der Installation).

```bash
chimera doctor / models / features    # Einrichtung prüfen, Modelle auflisten, optionale Fähigkeiten sehen
chimera chat                          # interaktiver Assistent, der sich über Runden hinweg merkt
chimera tui                           # Vollbild-Terminal-App
chimera run "PROMPT" --image pic.png  # Einmal-Antwort (kann ein Bild lesen)
chimera fuse "PROMPT" --show-panel    # mehrere Modelle verschmelzen: Panel -> Richter -> Synthesizer
chimera solve "TASK" --verify "pytest -q" --isolate   # eine Aufgabe erledigen; Änderung nur behalten, wenn die Prüfung besteht
chimera crew "TASK" --mode supervisor         # ein Team von Spezialisten geht eine Aufgabe an
chimera crew-isolated "TASK" -W "name:role" --verify "..." --synthesize   # Team, jeder in seiner eigenen isolierten Kopie
chimera explore "where is login handled?"     # die richtigen Dateien/Zeilen finden, eine kurze Antwort erhalten
chimera deliver "a launch plan" -o plan.md    # ein poliertes Dokument erzeugen
chimera serve --cron [--discord|--telegram|--slack|--signal]   # als Dienst betreiben: Chat-Bot + Scheduler
chimera cron add "brief" "0 8 * * *" "Summarize the news"       # wiederkehrende Arbeit planen
chimera memory add / graph / consolidate      # Langzeitgedächtnis: speichern, verknüpfen, aufräumen
chimera kanban add/board/run                   # ein Task-Board, das Arbeit an den Agenten verteilt
chimera workflow flow.yaml                     # eine wiederholbare Automatisierung ausführen, die in einer Datei beschrieben ist
chimera migrate <source> <dir> --apply         # Einstellungen, Fähigkeiten und Gedächtnis aus einem anderen Agenten-Werkzeug importieren
chimera evolve status / tune / recipe          # optional: selbst-optimieren; Daten vorbereiten, um ein Modell feinzujustieren
chimera fusion-bench / skillcard-bench / schema-bench / sandbox-bench   # ehrliche A/B-Benchmarks: Kosten, Qualität & Nebenwirkungen messen, bevor man einer Funktion vertraut
chimera pet new --name Chimi                   # einen kleinen virtuellen Begleiter adoptieren :)
```

Siehe den **[Nutzungsleitfaden](docs/usage.md)** für jeden Befehl mit Copy-Paste-Beispielen.

## Architektur

Chimera ist ein Python-Paket mit klar getrennten Teilen, sodass du jedes Stück für sich verstehen oder
erweitern kannst:

```
chimera/
  core/          die Agenten-Schleife: planen, handeln, verifizieren, behalten-oder-rückgängig und isolierte Arbeitskopien
  fusion/        die "Viele-Köpfe"-Engine: Panel -> Richter -> Synthesizer + der smarte Router
  memory/        Kurzzeit- / jüngstes / faktisches / Über-dich-Gedächtnis + ein Beziehungsgraph
  skills/        die eingebaute Fähigkeitsbibliothek und wie relevante Fähigkeiten gefunden werden
  evolution/     neue Fähigkeiten aus Erfolg lernen und die Erfahrung, aus der es lernt
  governance/    der Sicherheitskern (erlauben/warnen/blockieren/nachfragen), Audit-Protokoll und Änderungskontrollen
  orchestration/ Teams von Agenten: Rollen, Crews, isolierte parallele Worker, einheitliche Berichte
  ecosystem/     fortgeschrittene Selbstverbesserung: Agenten, die Agenten entwerfen, optionales Modelltraining
  kanban/        ein Task-Board, das dem Agenten Karten übergibt
  workflow/      eine wiederholbare Automatisierung in einer einfachen Datei beschreiben und ausführen
  tools/         eingebaute Werkzeuge (Dateien, Shell, Web, Suche) + Code-Ausführung
  sandbox/       Werkzeuge lokal oder in einem abgeschotteten Container ausführen
  integrations/  externe Werkzeuge und jede Web-API verbinden
  scheduler/     wiederkehrende Aufgaben + der Daemon, der sie pünktlich auslöst
  migration/     bring deine Einrichtung aus anderen Agenten-Werkzeugen mit
  providers/     eine Schnittstelle zu jedem Modell, mit Fallback und Schlüsselrotation
  interface/     die gemeinsame Konversations-Engine (genutzt von Chat, App und Bots)
  server/        das Messaging-Gateway und der HTTP-Endpunkt
  cli/           der `chimera`-Befehl
```

Siehe [docs/architecture.md](docs/architecture.md) für das vollständige Design.

## Vision & Ziele

**Chimeras Ziel ist einfach: ein KI-Agent, den jeder betreiben kann, der besser denkt, indem er viele
Modelle kombiniert, statt einem zu vertrauen, der wirklich besser wird, je mehr er benutzt wird, und
der dabei sicher und vollständig offen bleibt.**

Die meisten KI-Werkzeuge heute sind entweder klug-aber-vergesslich (sie verlieren alles, sobald der
Chat endet) oder leistungsfähig-aber-geschlossen (du kontrollierst sie nicht). Und viele, die
versuchen, sich "selbst zu verbessern", werden über lange Läufe unbemerkt *schlechter*. Chimera ist
unser Versuch eines anderen Weges:

- **Besseres Denken, keine höhere Rechnung** — kombiniere mehrere Modelle nur, wenn es hilft, sodass die Qualität steigt, ohne zu verschwenden.
- **Echtes Gedächtnis und echte Fähigkeiten** — merke dir, was wichtig ist, und verwandle wiederholte Arbeit in wiederverwendbare Fertigkeiten.
- **Verbesserung, die anhält** — dem langsamen Verfall widerstehen, der andere Agenten aushöhlt, indem es seine eigene Arbeit überprüft und den Zustand sicher außerhalb des Modells hält.
- **Sicher und transparent** — jede Aktion ist überprüfbar, und zerstörerische fragen zuerst nach.
- **Offen für alle** — kostenlos, unter Apache-2.0 lizenziert, gemeinschaftsgetrieben, kein Lock-in.

Es ist früh (Alpha), und Ehrlichkeit ist uns wichtig: Es ist im intensiven Produktivbetrieb noch nicht
bewiesen. Wenn dich diese Vision begeistert, würden wir uns über deine Hilfe freuen, sie zu erreichen.

## Entwicklung

```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev

uv run ruff check .      # Stil/Lint
uv run mypy chimera      # strikte Typprüfungen
uv run pytest -q         # die Testsuite
```

Beiträge sind sehr willkommen — Code, Doku, Ideen, Fehlerberichte. Beginne mit
[CONTRIBUTING.md](CONTRIBUTING.md) und unserem [Verhaltenskodex](CODE_OF_CONDUCT.md).
Ein Sicherheitsproblem gefunden? Siehe [SECURITY.md](SECURITY.md).

## Community

Hast du eine Frage, eine Idee oder möchtest du beitragen? **[Komm zu uns auf Discord](https://discord.gg/ACvBbrmguV)** — alle sind willkommen.

Lieber Reddit? Folge **[r/ChimeraAgent](https://www.reddit.com/r/ChimeraAgent/)** für Updates und Diskussionen.

## Unterstützen

Chimera ist kostenlos und open source, offen entwickelt. Wenn es dir hilft, kannst du die Entwicklung mit einer Spende unterstützen — jeder Beitrag zählt und wird sehr geschätzt. 💜

**[💜 Über Stripe spenden](https://donate.stripe.com/9B63cofM491m4SBfe177O00)**

## Lizenz

[Apache-2.0](LICENSE) — frei zu nutzen, zu ändern und darauf aufzubauen.
