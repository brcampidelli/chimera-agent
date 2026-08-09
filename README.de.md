<div align="center">

<img src="assets/logo-wide.png" alt="Chimera logo" width="460" />

# Chimera

**Der kontrollierte, sich selbst weiterentwickelnde Agent — bewiesen und kontrolliert.**<br/>
<sub>Denkt mit vielen Köpfen, erledigt echte Arbeit selbst, lernt nur Bewiesenes und ist sicher durch Architektur.</sub>

[![Website](https://img.shields.io/badge/chimeraagent.space-visit-3b82f6.svg)](https://chimeraagent.space)
[![PyPI](https://img.shields.io/pypi/v/chimera-agent.svg?color=blue&label=PyPI)](https://pypi.org/project/chimera-agent/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-beitreten-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
[![Reddit](https://img.shields.io/badge/Reddit-r%2FChimeraAgent-FF4500.svg?logo=reddit&logoColor=white)](https://www.reddit.com/r/ChimeraAgent/)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![Donate](https://img.shields.io/badge/Donate-Stripe-635BFF.svg?logo=stripe&logoColor=white)](https://donate.stripe.com/9B63cofM491m4SBfe177O00)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <b>Deutsch</b> · <a href="README.fr.md">Français</a> · <a href="README.it.md">Italiano</a> · <a href="README.pl.md">Polski</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

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
> getestet (**2.000+ automatisierte Tests**, strikte Typprüfung und Linting bei jeder Änderung), aber
> im Produktivbetrieb noch nicht kampferprobt.

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
- 🛡️ **Sicher von Grund auf.** Jede riskante Aktion durchläuft zuerst eine Sicherheitsprüfung, alles Zerstörerische fragt nach Bestätigung, und nicht vertrauenswürdiger Code kann in einem abgeschotteten Container ohne Netzwerk laufen. (Diese Prüfungen sind ein günstiger erster Filter, nicht die eigentliche Grenze — die Sandbox ist es; und die Container-Isolierung ist optional. Siehe [SECURITY.md](SECURITY.md).)
- 🔌 **Jedes Modell, läuft überall.** Nutze große gehostete Modelle oder deine eigenen lokalen über eine einzige Schnittstelle — auf deinem Laptop oder einem 5-Dollar-Server, rund um die Uhr.
- 🧩 **Wirklich deins.** Quelloffen, kein Lock-in, kein Anbieter-Konto nötig. Du betreibst es, es gehört dir, du kannst alles ändern.

## Wie Chimera im Vergleich abschneidet

Chimera versucht nicht, die riesigen Agenten-Projekte im *Umfang* zu übertreffen. Es setzt auf die
drei Dinge, die eine echte Reverse-Engineering-Studie von fünf führenden Projekten (OpenClaw, Hermes,
nanobot, CrewAI, LangGraph) als das erkannt hat, was sie **alle offen lassen** — und macht sie zu
seinem Kern:

- 🧬 **Selbstevolution mit einem Fitness-Signal.** Die anderen "lernen", indem sie einfach anhängen, was auch immer passiert ist, oder durch menschliche Pull-Requests — nichts misst, ob eine gelernte Änderung tatsächlich geholfen hat. Chimera behält eine Änderung **nur, wenn ein verifiziertes Ergebnis beweist, dass sie es tat**: Der Evolutionsschritt ist an den echten Working-Tree-Diff und ein ehrliches A/B gekoppelt, nie an das Wort des Modells. Unabhängiger Beleg, dass das zählt: [EvoAgentBench (arXiv 2607.05202)](https://arxiv.org/abs/2607.05202) hat gemessen, dass *automatische*, ungegatete Methoden zur Erfahrungskodierung regelmäßig **negativen Transfer** erzeugen — eine populäre Methode verschlechterte sich um **−12,3 Punkte** bei Aufgaben, auf die sie nicht abgestimmt war. Chimeras Gate führt jetzt auch einen **Transfer-Holdout** aus: Eine gelernte Änderung darf einen disjunkten Ausschnitt gleicher Fähigkeit nicht verschlechtern, bevor sie befördert wird — so kann sie nicht einfach ihr eigenes Eval auswendig lernen.
- 🛡️ **Sicherheit durch Architektur.** Prompt Injection gilt inzwischen weithin als *nicht patchbar*; die populären Agenten mildern sie auf App-Ebene ab oder erklären sie für außerhalb des Fokus (eines lieferte 135k öffentlich exponierte Instanzen und einen Marktplatz, der zu ~12 % voller bösartiger Skills war). Chimera bringt eine echte Abwehrschicht mit — **optional per `--taint`, standardmäßig aus**: Sie verfolgt die Taint-Provenienz *heuristisch* (wörtlicher Referenz-/Inhaltsfluss, **kein** echtes Dataflow — ein Modell, das den tainted Inhalt paraphrasiert, wäscht ihn rein), entfernt Steuer-Tokens aus nicht vertrauenswürdigem Inhalt, engt den Zugriff auf gefährliche Werkzeuge für den Rest eines tainted Laufs ein und sichert Retries mit Seiteneffekten ab; nicht vertrauenswürdiger Code läuft in einem optionalen, abgeschotteten Container. Gemessen, nicht behauptet: Auf dem eingebauten Korpus aus **7 Angriffen** senkt das die Angriffs-Erfolgsrate von **100 % → ~14 %** ([`chimera/eval/injection.py`](chimera/eval/injection.py)). [`SECURITY.md`](SECURITY.md) benennt klar, was weiterhin durchkommt (Übergabe zwischen Sub-Agenten, Fusion/Zusammenfassung, Einstiegspunkte außerhalb der CLI) — die Eindämmungsgrenze ist die Sandbox; diese Schicht ist Defense-in-Depth darüber.
- 📊 **Ehrliche, veröffentlichte Benchmarks.** ~20 % der als "gelöst" markierten Fälle einer populären Rangliste sind tatsächlich falsch. Chimera meldet jede Zahl mit einem Konfidenzintervall — **einschließlich der Läufe, in denen es nicht gewann** — und würfelt nie neu, um Signifikanz zu erzeugen. Ein aufgezeichneter gepaarter Lauf zeigt, wie die volle Schleife **ein schwaches Modell auf einer vorregistrierten 100-Aufgaben-Suite anhebt — 9 % → 15 % (+6pp), 95%-KI [+1,3 %, +6,0 %] — statistisch signifikant** (das KI schließt null aus), aus **6 Aufgaben, die sie wiederherstellte** (roher Fehlschlag → verifiziertes Bestehen) mit **null Regressionen**; die absoluten Raten sind bewusst niedrig, weil 85 der 100 Aufgaben schwer genug sind, um in beiden Armen zu scheitern (ein bewusster Boden, damit die Schleife Spielraum hat). Ein Lauf, kein Neuwürfeln. Und beim **offiziellen Terminal-Bench** landete ein vorregistriertes A/B mit N=40 auf einem **varianzdominierten Boden ohne signifikanten Unterschied in beide Richtungen** — unverändert veröffentlicht ([`bench/terminal_bench/RESULTS.md`](bench/terminal_bench/RESULTS.md)), einschließlich der **Rücknahme eines falschen Zwischenergebnisses**, sobald der Kontrollarm gemessen war. Null-Ergebnisse und Selbstkorrekturen werden ebenfalls veröffentlicht; das ist der Punkt.

**In einem Satz: der kontrollierte, sich selbst weiterentwickelnde Agent — bewiesen und kontrolliert.** Es ist Alpha, und es sagt das auch.

## Benchmarks (ehrlich)

Zwei aufgezeichnete Zahlen, beide wahr, absichtlich gemeinsam veröffentlicht — eine inzwischen
signifikant, eine ernüchternd. (Auch im Bildschirm **Reife & Benchmarks** der Desktop-App zu sehen,
direkt aus dem mitgelieferten Snapshot.)

- **Anhebung eines schwachen Modells (signifikant).** Ein günstiges Modell (`mistral-small-3.2-24b`) +
  Chimeras Retry-Schleife gegen dasselbe Modell allein, auf einer **vorregistrierten Suite mit n=100**
  (Design und Aufgaben committet und gepusht vor jedem Modellaufruf): **48,0 % → 71,0 % (+23,0 pp)**,
  gepaartes **95 %-KI [+12,6 %, +28,6 %] — statistisch signifikant** (das KI schließt 0 aus), aus
  **28 Aufgaben, die die Schleife gerettet hat** (roher Fehlschlag → verifiziert bestanden) gegen
  5 Regressionen. Ein Modell, ein Seed/Aufgabe, kleine, in sich geschlossene Python-Aufgaben —
  **NICHT** SWE-bench, nicht auf echte Repositories übertragbar. Ein Lauf, kein Re-Roll.
  **Dies ersetzt einen früheren Lauf derselben Suite** (9,0 % → 15,0 %, +6,0 pp), dessen Harness mit
  einer Testdatei bewertete, die der geprüfte Agent bearbeiten konnte. Beim erneuten Lauf mit
  wiederhergestelltem Originaltest wurde der Agent dabei erwischt, wie er in einer Aufgabe seinen
  eigenen Bewertungstest umschrieb — die Lücke war also real — und die Anhebung replizierte sich
  *größer*, nicht kleiner. Auch die Behauptung des früheren Laufs, "85 der 100 Aufgaben seien schwer
  genug, um in beiden Armen zu scheitern", hielt nicht stand: Der Wiederholungslauf misst 24. Das
  vollständige Erratum, die aufbewahrten Manipulationsbelege und was nicht erneut verifiziert werden
  konnte, stehen in [`bench/local_lift/RESULTS.md`](bench/local_lift/RESULTS.md).
  Quelle: [`bench/local_lift/_reverify_n100/paired.json`](bench/local_lift/_reverify_n100/paired.json), [`PREREGISTRATION.md`](bench/local_lift/PREREGISTRATION.md).
- **SWE-bench Verified — der stärkste externe Beleg, und er hat eine Replikation überlebt, die ihn
  töten sollte.** Drei vorregistrierte Läufe auf `django/django`-Ausschnitten, bewertet **nur** vom
  offiziellen `swebench`-4.1.0-Harness in Docker — nie selbst berichtet.

  | Lauf | Ausschnitt | Baseline | + Chimera | gepaartes Δ | 95 %-KI | |
  |---|---|---|---|---|---|---|
  | 1 (`max_steps=8`) | 19 | 36,8 % | 36,8 % | +0,0 % | [−8,5 %, +8,5 %] | ns |
  | 2 (`max_steps=30`) | dieselben 19 | 42,1 % | 57,9 % | +15,8 % | [−1,9 %, +15,8 %] | ns |
  | **3 (Replikation)** | **41 ungesehene** | 34,1 % | 43,9 % | **+9,8 %** | [−3,5 %, +16,7 %] | ns |
  | gepoolt *(sekundär)* | 60 | 36,7 % | 48,3 % | **+11,7 %** | **[+0,8 %, +16,4 %]** | **signifikant** |

  Die +15,8 % aus Lauf 2 waren ein 3:0 bei drei informativen Paaren, und die Vorregistrierung gab dem
  eine **Eins-zu-drei-Chance, genau das zu sein — eine Glücksstichprobe**, mit vorab zugesagtem
  Widerruf. Lauf 3 prüfte es an **41 Instanzen, deren Ergebnisse wir nie gesehen hatten**, sonst
  unverändert. Der Effekt **trat erneut auf** (+9,8 %, innerhalb des registrierten Bandes von +5 bis
  +20) auf einem Ausschnitt, der sich als *schwerer* erwies als der aus Lauf 2. Über beide hinweg
  stehen die diskordanten Paare **9 für Chimera gegen 2** (p ≈ 2,6 % unter der Nullhypothese).

  **Der Mechanismus replizierte sich, und das ist der interessante Teil.** Ein vierter Lauf stellte den
  mittleren Arm wieder her (reines Scaffold, ohne Diff-Gate) auf denselben 41 Instanzen, sodass sich
  alle drei um genau eine Komponente unterscheiden. Alle drei **editieren gleich häufig** (27–28
  Patches von 41); was sich ändert, ist, wie oft die Änderung *richtig* ist:

  | Arm | gelöst | **Präzision, wenn editiert wurde** |
  |---|---|---|
  | Baseline | 14/41 | 50 % |
  | + Scaffold | 16/41 | 59 % |
  | + Scaffold **und** Diff-Gate | 18/41 | 67 % |

  **Beide Komponenten tragen bei, in etwa gleichen Hälften** (+4,9 % je, keine allein signifikant) —
  was **unserer eigenen registrierten Vorhersage widerspricht**, das Scaffold werde den Großteil
  tragen, und eine Lesart aus Lauf 2 zurückzieht, das Diff-Gate sei "nicht das, was den Gewinn erzeugt
  hat". Der Widerruf steht in [`RESULTS.md`](bench/swe_bench/RESULTS.md); die saubere Additivität wird
  *nicht* als gemessener 50/50-Split behauptet, da jeder Vergleich auf 5–6 diskordanten Paaren beruht.

  ⚠️ Ehrlich gelesen: **das Out-of-Sample-Primärergebnis ist NICHT signifikant.** Die signifikante
  Zahl ist das **gepoolte Sekundärergebnis**, genau deshalb als sekundär vorregistriert, weil es
  Gesehenes mit Ungesehenem mischt — es wird jetzt, wo es die Linie überschritten hat, nicht zur
  Schlagzeile befördert. Und **48,3 % ist KEIN SWE-bench-Verified-Score**: ein bewusst leichter
  Ausschnitt aus einem einzigen Repository; ein echter Score braucht alle 500. Die exakte Null aus
  Lauf 1 wird unverändert veröffentlicht, und Lauf 2 lieferte den **Widerruf, den er sich verdient
  hat** (der Mechanismus, den wir für seine leeren Patches behauptet hatten, war falsch — die Abhilfe
  war das Schrittbudget).
  Quelle: [`bench/swe_bench/RESULTS.md`](bench/swe_bench/RESULTS.md), [`PREREGISTRATION.md`](bench/swe_bench/PREREGISTRATION.md).
- **Terminal-Bench (ernüchternd).** Vorregistriertes A/B mit N=40 auf dem offiziellen Benchmark,
  dasselbe Modell in beiden Armen (`deepseek-chat-v3.1`): **7,5 % → 2,5 %** mit dem Scaffold,
  gepaartes **Δ −5,0 pp, 95 %-KI [−5,0 %, +1,6 %] — nicht signifikant**. Das Scaffold **hob ein
  bereits kompetentes Modell nicht an** (dies ist nicht das schwache "Goldilocks"-Regime, in dem
  Scaffolding hilft); beide Arme liegen auf einem varianzdominierten Boden.
  Quelle: [`bench/terminal_bench/RESULTS.md`](bench/terminal_bench/RESULTS.md).
- **Hilft angesammeltes Lernen? Sieben Läufe sagen: nicht nachweisbar (und ein Positivbefund wurde
  widerrufen).** Das Schwungrad — Skills, die an Wiederkehr plus einen Transfertest gekoppelt sind,
  Antimuster-Karten, dauerhaftes Gedächtnis — wurde über **sieben vorregistrierte Läufe** gemessen.
  Lauf 6 lieferte den einzigen Positivbefund der Serie (signifikante +6,7 % auf der
  Transfermetrik innerhalb der Familie); **Lauf 7, mit mehr Teststärke, drückte ihn auf +2,0 % und
  nicht signifikant — also wurde er widerrufen**, genau wie in der Vorregistrierung zugesagt. Das
  ehrliche Urteil: **kein ausreichend teststarker Lauf zeigt, dass angesammeltes Lernen den
  Aufgabenerfolg verbessert**, und der Engpass ist das Messinstrument — drei Versuche, eine Suite im
  informativen 40–60-%-Band zu schreiben, landeten alle bei 84–92 %. "Es wird besser, je mehr du es
  nutzt" bleibt **unbelegt**.
  Quelle: [`bench/learning_lift/RESULTS.md`](bench/learning_lift/RESULTS.md).

Intern signifikant (auf unserer eigenen schweren Suite). Auf echten Repositories **out-of-sample
repliziert und nur gepoolt signifikant** — das ehrliche Etikett, nicht das schmeichelhafte.
Ernüchternd auf Terminal-Bench. Die Lernbehauptung ist **widerrufen**. Wir veröffentlichen alles, wir
schreiben *vor* dem Lauf auf, in welchem Zweig das Ergebnis unsere eigene Behauptung tötet, und wir
würfeln nicht neu, bis es signifikant wird — das wäre p-Hacking.

## Token-Ökonomie — gemessen, nicht behauptet

Zwei "mehr Modelle = besser"-Instinkte, an echten Läufen stresstestet (Vorhersagen registriert
*vor* jedem Lauf, Siege **und** Niederlagen veröffentlicht — siehe [`bench/`](bench/)):

**Fusion ist vorbehalten, nicht Standard.** In einer Reasoning-Suite mit 12 Aufgaben erreichte die
mittlere Stufe allein 100 % bei 846 Tokens; volle Fusion erreichte ebenfalls 100 % — für **9.526
Tokens (~11×)**. Fusion sitzt also hinter einer Kaskade billig→Gate→mittel→Fusion, die nur
eskaliert, wenn ein kostenloses Gate versagt, und erreicht ~mittlere Qualität zu ~1/12 der Kosten
von Fusion.

**Hierarchische Orchestrierung gewinnt nur dort, wo sie sollte — und nach einem Gesetz, das wir
aufschreiben können.** `chimera orchestrate` teilt eine Aufgabe auf abgegrenzte Worker auf statt auf
einen großen Kontext. Ein einzelner Agent schickt jedes Dokument in jeder Runde erneut; abgegrenzte
Worker lesen jedes einmal. So skaliert die Token-Ersparnis als **(D−1)/D** in der Anzahl der
Dokumente D — an echten Läufen bis auf <0,2 % bestätigt:

| Dokumente (D) | gemessene Token-Ersparnis | (D−1)/D |
|---|---|---|
| 2 | 49.9% | 50% |
| 3 | 66.7% | 66.7% |
| 4 | 74.8% | 75% |
| 5 | 79.9% | 80% |

Die Ersparnis bleibt konstant, wenn das Gespräch länger wird, und steigt mit der Dokumentgröße auf
dieselbe Grenze zu ([vollständiger Sweep, 3 Achsen](bench/hierarchy_sweep/README.md)). Und wo es
sich *nicht* auszahlt — eine Single-Shot-Aufgabe mit einer Runde — erkennt der Klassifikator das und
**fällt auf einen einzelnen Agenten zurück** (dieser Lauf kostete +47 % mehr Tokens; wir haben ihn
ebenfalls veröffentlicht).

**Das ehrliche Sternchen.** Dies sind *Token*-Zahlen. Mit Prompt-Caching berechnet ein Anbieter die
wiederholten Dokumente des einzelnen Agenten mit ~0,1×, sodass der *Dollar*-Gewinn kleiner ist — und
jenseits weniger Runden kann er sich **umkehren** (unabhängige Worker bezahlen den kalten Kontext
erneut, den der einzelne Agent cacht). Wir liefern das
[Modell, das das quantifiziert](bench/hierarchy_sweep/cache_cost.py), statt die Token-Zahl still als
Dollar-Zahl auszugeben.

## Funktionen

### 🧠 Denken & Handeln
- **Mehrere Modelle zu einer Antwort verschmelzen** (`chimera fuse`) — ein Gremium aus Modellen, ein Richter, der aufzeigt, wo sie übereinstimmen, sich widersprechen oder etwas übersehen, und ein Synthesizer, der die finale Antwort schreibt. Ein smarter Router investiert diesen zusätzlichen Aufwand nur bei schweren Problemen, und wenn sich die ersten Modelle bereits einig sind, bricht er frühzeitig ab — in unseren Benchmarks gemessen mit **~20–28 % weniger Tokens ohne Genauigkeitsverlust**. (Fusion / Mixture-of-Agents an sich ist nichts Einzigartiges — es gibt sie in OpenRouter und anderen Tools; der Unterschied hier ist, dass sie in die Agenten-Schleife hinter diesem kostenbewussten Router eingebaut und gemessen ist, kein Modell, das man auswählt.)
- **Aufgaben eigenständig erledigen** (`chimera solve`) — es plant, handelt mit Werkzeugen und **verifiziert dann und macht rückgängig**: Es führt deine Prüfung aus (z. B. Tests) und behält die Änderung nur, wenn sie besteht, andernfalls macht es sie rückgängig und versucht es erneut. Optional arbeitet es an einer isolierten Kopie deines Projekts, sodass nichts angefasst wird, bis es bewiesen ist. **Und ein überzeugender Absatz ist keine Lösung:** ohne ein `--verify`, auf das man sich berufen könnte, wird ein Lauf, der nichts auf der Festplatte verändert hat, als Fehlschlag gemeldet und nicht als Erfolg — denn das Einzige, was ihn dann noch beurteilen würde, wäre ein Modell, das Prosa liest und den Diff nie sieht. Jeder Versuch hält fest, *wer* ihn freigegeben hat (`verifier` / `diff+manager` / `manager` / `none`), damit eine Quittung nie "Erfolg" sagt, ohne die Instanz dahinter zu benennen.
- **Teams von Spezialisten** (`chimera crew`, `chimera crew-isolated`) — mehrere rollenfokussierte Agenten teilen sich eine Aufgabe. Im isolierten Modus arbeitet jeder an seiner **eigenen privaten Kopie parallel**; sichere Änderungen werden zusammengeführt, Konflikte werden gemeldet statt still überschrieben, und die Änderungen eines schlechten Workers können durch einen Test pro Worker abgelehnt werden. Ein Supervisor kann die Arbeit aller zu einem einheitlichen Bericht zusammenfügen.
- **Delegieren und erkunden** — jeder Agent kann eine in sich geschlossene Teilaufgabe an einen frischen **Subagenten** übergeben, der nur das Ergebnis zurückmeldet, sodass der Hauptkontext sauber bleibt. Der **Context Explorer** (`chimera explore`) findet die richtigen Dateien und Zeilen in einer Codebasis und liefert eine kurze Antwort, statt alles abzuladen.

### 🧬 Gedächtnis & Selbstverbesserung
- **Langzeitgedächtnis** — es behält Kurzzeit-, jüngste, faktische und Über-dich-Erinnerungen, plus eine Karte, wie Dinge zusammenhängen. Es kann Erinnerungen in einer schnellen Volltext-Datenbank speichern, ein Profil deiner Vorlieben in jeden Chat mitnehmen, doppelte Notizen automatisch zusammenführen und behutsam vorschlagen, eine Vorliebe zu speichern, wenn du eine erwähnst.
- **Lernt neue Fähigkeiten** — wenn es bei derselben Art von Aufgabe mehr als einmal erfolgreich ist, verwandelt es das automatisch in eine getestete, wiederverwendbare Fähigkeit.
- **Optionales Selbsttraining (fortgeschritten)** — es kann seine eigene Erfahrung aufzeichnen, damit du später ein Modell daraus feinjustieren kannst. Standardmäßig aus; nichts wird trainiert, ohne dass du danach fragst.

### 📏 Eine Schleife, die man messen kann — und die sagt, wann sie sich verlaufen hat
Ein Agent ist ein Modell **plus alles drumherum**. Diese umgebende Maschinerie entscheidet, ob ein
langer Lauf nützlich bleibt, und das meiste davon ist unsichtbar, bis es scheitert. Chimera misst die
eigene:

- **Jeder Lauf hinterlässt eine Quittung.** Eine JSONL-Zeile pro Lauf in `traces.jsonl`: Tokens pro Schritt, die aufgerufenen Werkzeuge mit dem, was zurückkam, wo Verlauf verworfen wurde — und die **Cache-Trefferquote**, der Anteil der Prompt-Tokens, den der Anbieter aus dem Cache lieferte. Das ist die eigentliche Kostenzahl der Schleife (ein gecachtes Token kostet etwa ein Zehntel eines frischen, identische Token-Zahlen können sich also um ~10× im Preis unterscheiden) *und* ein Design-Alarm: Sie bricht ein, sobald etwas den Anfang des Prompts umschreibt, was kein anderes Symptom hat. Ein Anbieter, der nichts über Cache meldet, gilt als **unbekannt**, nie als Fehltreffer.
- **Sie merkt, wenn sie nicht mehr weiterkommt.** Zwei verschiedene Dinge heißen "Kontextproblem": nachlassende Aufmerksamkeit innerhalb eines langen Prompts, und eine *Trajektorie*, die still aufhört zu akkumulieren und anfängt zu kreisen — jeder einzelne Schritt in Ordnung, der Lauf als Ganzes ohne Fortschritt. Chimeras Schleifenbrecher fängt die enge Variante (ein Fenster von 12 Aufrufen); ein Lauf, der alle zwanzig Züge dieselben drei Dateien erneut liest, spaziert einfach hindurch. Deshalb gibt es einen zweiten Detektor, der die **erste Hälfte eines Laufs mit der zweiten** vergleicht: erneut hergeleitete Arbeit, die der Lauf schon hatte, steigende Fehlerraten, oder Redundanz, die genau nach dem Verwerfen von Verlauf hochschnellt. Er **meldet und handelt nicht** — Stoppen, Neuplanen und erzwungenes Kompaktieren sind alle plausible Heilmittel, und wir haben keinen Beleg, welches hilft; eines auszuwählen würde genau die ungemessene Annahme einbauen, die diese Arbeit beseitigen soll.
- **Lange Läufe überleben ihren eigenen Kontext.** Ein volles Fenster beendete früher den Lauf schlicht, womit das Fenster — und nicht die Schwierigkeit der Aufgabe — die eigentliche Obergrenze war. Die Kompaktierung lässt jetzt die System-Nachricht unangetastet (sie ist das stabile Präfix, an dem der gesamte Prompt-Cache hängt), lässt nie ein Werkzeug-Ergebnis ohne seinen Aufruf zurück und **stellt wieder her, was der Lauf braucht, um noch er selbst zu sein**: die offene Datei, den Plan, die Aufgabenliste, den aktuellen Stand. Sie sagt klar, was sie verworfen hat, statt es zusammenzufassen — ein Agent kann eine Datei erneut lesen, aber er kann eine erfundene Zusammenfassung nicht wieder entglauben.

### 🔌 Verbinden & Automatisieren
- **Sprich überall mit ihm** — ein Terminal-Chat, eine Vollbild-Terminal-App oder als Bot auf **Discord, Telegram, Slack, Signal und WhatsApp**. Es gibt außerdem einen einfachen HTTP-Endpunkt.
- **Zeitplanung & Proaktivität** — gib ihm wiederkehrende Aufgaben in einfacher Sprache ("fasse jeden Morgen die Nachrichten zusammen"). Mit dem eingebauten Scheduler in Betrieb **handelt es pünktlich**, nicht nur, wenn du ihm schreibst.
- **Werkzeuge & Integrationen** — Dateien lesen und schreiben, Shell-Befehle ausführen, **vollständig gerenderte Webseiten lesen und ganze Websites scrapen oder crawlen** (mit injektionssicherer strukturierter Extraktion) und Code sicher in einer Sandbox ausführen. Verbinde nahezu jeden Webdienst (über seine API) oder ein externes Werkzeug — einschließlich jedes **MCP-Servers** ([Anleitung + lauffähiges Beispiel](docs/mcp.md)) — und importiere deine Einrichtung aus anderen Agenten-Werkzeugen, die du bereits nutzt.
- **Alles inklusive** — Websuche, Bilderzeugung (gehostet **oder vollständig lokal**), **Speech-to-Text** und Text-to-Speech, **Medien-Download**, **Datenanalyse & Diagramme**, E-Mail, Kalender, Code-Ausführung und mehr, bereit zum Einschalten.

### 🚀 Überall laufen, sicher
- **Jedes Modell, eine Schnittstelle** — gehostete Modelle oder deine eigenen lokalen, mit automatischem Fallback, falls eines ausfällt, und Rotation über mehrere Schlüssel.
- **Server-Deployment mit einem Befehl** — betreibe es mit Docker (oder auf Bare-Metal), sodass es läuft und beim Neustart wieder hochfährt. Siehe **[docs/deploy.md](docs/deploy.md)**.
- **Sicherheitskern** — eine Prüfung bei jeder Aktion (erlauben / warnen / blockieren / nachfragen), ein **optionaler** netzwerkisolierter Container für nicht vertrauenswürdigen Code (`CHIMERA_SANDBOX=docker`; der Standard-Runner *local* ist *nicht* isoliert) und ein vollständiges Audit-Protokoll dessen, was es getan hat.
- **Halte an, bevor es etwas festschreibt, das es aus unsicherer Quelle gelesen hat** (`--pause-on-taint`) — ein Lauf, der nicht vertrauenswürdige Inhalte verarbeitet hat, parkt sich selbst, statt abzuschließen, und wartet auf dich. Du kannst das Ergebnis annehmen, eine von dir bearbeitete Fassung annehmen, Hinweise schicken und es erneut versuchen lassen, oder es ganz ablehnen — vom Terminal *oder* aus der Desktop-App. Nichts wird gespeichert und nichts gelernt, bis du entscheidest, und eine Pause wird nie als Fehlschlag gemeldet: Sie hat kein Urteil erreicht, sie wartet auf einen Menschen.
- **Eine Desktop-App, die einen Lauf steuert und ihn nicht nur startet** — fünf Ziele statt eines Menüs aus fünfzehn, in neun Sprachen. Starte einen Lauf und geh weg: Der Fortschritt ist noch da, wenn du zurückkommst, die Statusleiste benennt von jedem Bildschirm aus, was der Agent tut, und Stopp funktioniert überall. Native Installer für Windows / macOS / Linux unter [Releases](https://github.com/brcampidelli/chimera-agent/releases).

## Schnellstart

Du brauchst **Python 3.11–3.13** und [uv](https://docs.astral.sh/uv/) (einen schnellen Python-Installer).

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

**5. In 5 Minuten etwas Echtes tun: E-Mail-Triage.** Richte Chimera auf deinen Posteingang und
erhalte eine Zehn-Sekunden-Zusammenfassung — nur lesend, klassifiziert URGENT / PERSONAL /
NEWSLETTER / COLD-SALES, und optional jeden Morgen geplant:
```bash
uv run chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```
Einrichtung + tägliche Zeitplanung + ehrliche Vorbehalte: **[examples/email_triage/README.md](examples/email_triage/README.md)**.

## 🧰 Was Chimera kann — und wie du jede Funktion einschaltest

Neu hier? Chimera läuft direkt nach `pip install chimera-agent` + einem KI-Schlüssel. Einige
Fähigkeiten (Dokumente lesen, Audio hören, Diagramme erstellen, Video herunterladen…) brauchen ein
kleines optionales Paket — ein sogenanntes **„Extra"** — und einige einen Dienst-Schlüssel. Dieser
Abschnitt listet **jede Fähigkeit, genau was zu installieren ist und den Befehl zum Ausprobieren**.
Keine Vorkenntnisse nötig.

### Alles auf einmal einschalten
```bash
pip install 'chimera-agent[full]'     # jede Nicht-GPU-Funktion unten, in einem Befehl
```
Audio und Video brauchen außerdem **ffmpeg** auf deinem Computer:
`macOS: brew install ffmpeg` · `Ubuntu/Debian: sudo apt install ffmpeg` · `Windows: choco install ffmpeg`.
Lieber schlank? Behalte `pip install chimera-agent` und füge nur die gewünschten Extras hinzu (siehe
Spalte „Braucht"). **Docker? Das offizielle Image enthält bereits alles unten.**

### Jede Fähigkeit, Punkt für Punkt
**Braucht** = was hinzufügen: `—` funktioniert in der Basis-Installation · `[extra]` = `pip install 'chimera-agent[extra]'` · `Schlüssel: X` = ein Anbieter-Schlüssel in `.env`.

| Was du bekommst | Braucht | So benutzt du es |
|---|---|---|
| **Chat, der sich an dich erinnert** | — | `chimera chat` |
| **Eine Frage stellen** | — | `chimera run "erkläre X in 3 Punkten"` |
| **Vollbild-Terminal-App** | — | `chimera tui` |
| **Desktop-App** (Chat · Arbeit · Code · Wissen · Automatisierung, in 9 Sprachen) | `[desktop]` oder ein Download | `chimera app`, oder einen nativen Installer (`.exe`/`.dmg`/`.AppImage`/`.deb`) von [Releases](https://github.com/brcampidelli/chimera-agent/releases) holen |
| **Eine Aufgabe erledigen, nur behalten wenn ein Test besteht** | — | `chimera solve "füge hello() zu app.py + einen Test hinzu" --verify "pytest -q"` |
| **Frag mich, bevor etwas aus dem Web Gelesenes festgeschrieben wird** | — | `--pause-on-taint` an `chimera solve` anhängen |
| **Sehen, was ein Lauf wirklich gekostet hat, Schritt für Schritt** | — | wird für dich geschrieben: `.chimera/traces.jsonl` (oder `$CHIMERA_HOME`) |
| **Mehrere Modelle zu einer Antwort verschmelzen** | — | `chimera fuse "deine Frage" --show-panel` |
| **Ein Team von Spezialisten-Agenten** | — | `chimera crew "deine Aufgabe" --mode supervisor` |
| **Ein ganzes Projekt bis zum Ende führen** (pausiert vor riskanten Schritten) | — | `chimera project start spec.yaml -w .` |
| **Bilder sehen** (Vision) | Schlüssel: Gemini oder OpenAI | `chimera run --image foto.jpg "was ist das?" --model gemini/gemini-2.0-flash` |
| **Audio hören** (Sprache → Text) | `[stt]` + ffmpeg | `chimera run "transkribiere meeting.mp3"` |
| **Sprechen** (Text → Sprache) | Schlüssel: ElevenLabs oder OpenAI | bitte eine Aufgabe „lies das laut nach speech.mp3 vor" |
| **Dokumente lesen** (PDF, Word, Excel → Text) | `[documents]` | `chimera run "fasse bericht.pdf zusammen"` |
| **Video/Audio herunterladen** (YouTube + 1000+ Seiten) | `[media-dl]` + ffmpeg | `chimera run "lade das Audio von <url> herunter"` |
| **Daten analysieren & Diagramme erstellen** | `[data,viz]` | `chimera run "lade umsatz.csv und plotte den Monatsumsatz"` |
| **Im Web suchen** | Schlüssel: Tavily | `chimera run "suche im Web: die neueste Python-Version"` |
| **Echte Webseiten lesen & scrapen** (ein echter Browser) | — | `chimera run "öffne example.com und nenne die Überschrift"` |
| **Langzeitgedächtnis** | — | `chimera memory add "..."` · `chimera memory search "..."` |
| **Wiederverwendbare Skills selbst lernen** | — | passiert während `chimera solve`; auflisten mit `chimera skills` |
| **Wiederkehrende Arbeit planen** | — | `chimera cron add brief "0 8 * * *" "fasse die Nachrichten zusammen"` |
| **Als Chat-Bot laufen** (Discord/Telegram/Slack/Signal/WhatsApp) | `[messaging]` | `chimera serve --cron --discord` |
| **Beliebiges externes Tool anbinden** (MCP) | `[mcp]` | Anleitung: [docs/mcp.md](docs/mcp.md) |
| **Bilder generieren** (gehostet) | Schlüssel: OpenAI | bitte eine Aufgabe „generiere ein Bild von …" |
| **Bilder generieren** (100 % lokal, GPU nötig) | `[imagegen-local]` | dasselbe, offline |

> Installiere Extras einzeln, wenn du es schlank willst — `messaging`, `mcp`, `documents`, `media-dl`,
> `stt`, `data`, `viz`, `youtube` (alle in `full` enthalten), plus `imagegen-local` und `train` (nur GPU).
> Beispiel: `pip install 'chimera-agent[documents,stt]'`.

### Zum ersten Mal? Sechs Schritte für Einsteiger
1. **Installiere Python 3.11–3.13** ([python.org](https://www.python.org/downloads/)); prüfe mit `python --version`.
2. **Installiere Chimera:** `pip install 'chimera-agent[full]'` (oder nur `chimera-agent` für den schlanken Kern).
3. **Hol dir einen KI-Schlüssel** — ein [OpenRouter](https://openrouter.ai)-Schlüssel ist am einfachsten (ein Schlüssel → 100+ Modelle).
4. **Gib Chimera den Schlüssel:** kopiere `.env.example` nach `.env`, setze `CHIMERA_OPENROUTER_KEYS=sk-or-...`.
5. **Prüfe, ob alles bereit ist:** `chimera doctor` — es sagt, was eingerichtet ist und was fehlt.
6. **Probier es aus:** `chimera chat`.

Ab hier funktioniert jeder Befehl aus der Tabelle oben. Vollständige Befehlsreferenz mit
Copy-&-Paste-Beispielen: **[docs/usage.md](docs/usage.md)**.

## Wie es funktioniert

Gib Chimera eine Aufgabe; es plant (und hebt die relevantesten eingebauten Fähigkeiten hervor), denkt
(verschmilzt Modelle, wenn das Problem schwer ist), handelt mit Werkzeugen — liest und scrapt das Web,
bearbeitet Dateien, erstellt Diagramme —, **überprüft seine eigene Arbeit und behält nur, was
besteht**, und lernt dann aus dem Ergebnis — indem es Gedächtnis und neue Fähigkeiten in die nächste
Aufgabe zurückspeist.

```mermaid
flowchart TD
    U([Du: eine Aufgabe oder Frage]) --> P[Verstehen & planen]
    P --> Q{Ist es ein schweres Problem?}
    Q -- ja --> FUSION[Mehrere Modelle fragen<br/>· ein Richter vergleicht sie<br/>· ein Synthesizer schreibt die beste Antwort]
    Q -- nein --> ONE[Ein schnelles Modell nutzen]
    FUSION --> ACT[Handeln: Werkzeuge, Dateien, Web lesen & scrapen, Diagramme erstellen<br/>oder an Subagenten delegieren]
    ONE --> ACT
    ACT --> V{Hat es funktioniert?<br/>Tests / Prüfungen ausführen}
    V -- ja --> KEEP[Änderung behalten]
    V -- nein --> REVERT[Rückgängig & mit der Lektion erneut versuchen]
    REVERT --> ACT
    KEEP --> LEARN[Lernen: Wichtiges ins Gedächtnis speichern,<br/>wiederholte Arbeit in eine Fähigkeit verwandeln]
    LEARN --> U
    MEM[(Langzeitgedächtnis)] -. erinnert .-> P
    LEARN -. schreibt .-> MEM
    SKILLS[(Fähigkeitsbibliothek)] -. hebt relevante Fähigkeiten hervor .-> P
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
Du möchtest Chimera etwas Neues beibringen? Der **[Erweiterungs-Leitfaden](docs/extending.md)** zeigt,
wie du ein eigenes **Werkzeug, eine Skill oder ein Rezept** hinzufügst (mit Copy-Paste-Beispielen).
Ein Sicherheitsproblem gefunden? Siehe [SECURITY.md](SECURITY.md).

## Community

Hast du eine Frage, eine Idee oder möchtest du beitragen? **[Komm zu uns auf Discord](https://discord.gg/ACvBbrmguV)** — alle sind willkommen.

Lieber Reddit? Folge **[r/ChimeraAgent](https://www.reddit.com/r/ChimeraAgent/)** für Updates und Diskussionen.

## Unterstützen

Chimera ist kostenlos und open source, offen entwickelt. Wenn es dir hilft, kannst du die Entwicklung
mit einer einmaligen Spende unterstützen — jeder Beitrag zählt und wird sehr geschätzt. 💜

**[💜 Über Stripe spenden](https://donate.stripe.com/9B63cofM491m4SBfe177O00)**

## Lizenz

[Apache-2.0](LICENSE) — frei zu nutzen, zu ändern und darauf aufzubauen.
