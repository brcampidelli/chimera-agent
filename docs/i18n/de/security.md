---
source_sha256: eeb0e80877d9cd939362a1d6f1b736437c3918f1b24f1fb1b44e18f31aa71e42
---

# Sicherheit & Schutzmaßnahmen

Chimera kann Shell-Befehle ausführen, Dateien bearbeiten, APIs aufrufen und seine eigenen
Skills modifizieren. Es liefert **Defense-in-Depth**, und — das zählt — die Dokumentation sagt,
wo jede Schicht *aufhört*.

!!! warning "Die eine Regel"
    Keine dieser Schutzmaßnahmen ersetzt das **Ausführen in einer isolierten Umgebung**, wenn
    Autonomie gewährt wird. Der Standard-Runner `local` ist nicht isoliert; für nicht
    vertrauenswürdige Arbeit `CHIMERA_SANDBOX=docker` nutzen (Netzwerk aus, optional unter
    gVisor).

## Die Schichten

- **Governance-Kernel** — jeder kontrollierte Tool-Aufruf ist allow / warn / review / block.
  Ein günstiger erster Filter für gefährliche Shell-Signaturen, nicht die Grenze.
- **Sandbox** — ein flüchtiger, netzwerkloser Container (`CHIMERA_SANDBOX=docker`), härtbar
  mit gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`).
  Der **verify-Befehl** läuft in derselben Sandbox wie die Shell des Agenten, und wenn diese
  Sandbox nicht isoliert ist, durchläuft ein Befehl, der nicht selbst getippt wurde — aus dem
  Repository abgeleitet, aus einem Cron-Job, einer Karte oder einem Workflow gelesen — dieselbe
  `CHIMERA_HOST_EXEC`-Bestätigung; wird sie abgelehnt, enthält er sich, statt zu laufen
  (`CHIMERA_VERIFY_NETWORK=1` gibt einem Docker-Verifier das Netzwerk; bei den Kernel-Sandboxes
  geht das nicht, ein Verifier, der das Netzwerk braucht, läuft also auf dem Host — und nur, wenn
  er selbst getippt wurde).
- **Tool-Allowlist pro Sitzung** — einem Lauf nur die Tools gewähren, die er braucht; die
  restlichen werden vollständig aus dem Schema des Modells entfernt.
- **Taint-Tracking** (`--taint`) — nicht vertrauenswürdiger Inhalt wird als Daten eingezäunt,
  seine Herkunft folgt ihm in Memories und Skills (ein Skill aus einem kontaminierten Lauf wird
  zur Review zurückgehalten), und sobald ein Lauf kontaminiert ist, verengen sich die
  gefährlichen Tools.
- **Reader unter Quarantäne** — das Dual-LLM-/CaMeL-Muster: nicht vertrauenswürdiger Inhalt wird
  von einem werkzeuglosen Modell gelesen, das nur schema-validierte Felder ausgeben kann, sodass
  eine Injection keine neue Anweisung oder keinen neuen Tool-Aufruf erzeugen kann.
- **Cross-Agent-Monitor** — bei Fan-out ist ein Monitor pro Worker blind gegenüber einem
  *aufgeteilten* Ablauf (ein Worker holt nicht vertrauenswürdigen Inhalt, ein anderer Worker
  senkt ihn ab — der Fetch und der Sink leben in getrennten Ledgern). Ein Aggregat-Monitor sieht
  das gesamte Fan-out; er ist **immer aktiv** für `solve-batch` / `crew-isolated`.

## Fan-out: der Cross-Agent-Monitor

Wenn mehrere werkzeugnutzende Worker parallel laufen (`solve-batch`, `crew-isolated`), erhält
jeder sein eigenes Capability-Ledger, und nach dem Batch läuft ein Aggregat-Monitor über alle.
Er erkennt Muster, die kein Einzel-Worker-Monitor sehen kann — die aufgeteilte Exfiltration, bei
der Worker A nicht vertrauenswürdigen Inhalt holt und Worker B ihn ausführt oder exfiltriert:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

Er **eskaliert immer nur zur Review** — er blockiert einen Lauf nie — und ist reine
Beobachtbarkeit (zeichnet Änderungen auf, ohne Verhalten zu beeinflussen). `--taint` zusätzlich
setzen, um auch die adaptive Allowlist jedes Workers zu aktivieren (bei Kontamination
gefährliche Tools brauchen dann Freigabe).

**Freigabe, und wie ein abgelehnter Worker aussieht.** Jeder Worker trägt seinen eigenen
Freigeber und seine eigene Aufzeichnung dessen, was er tun durfte — eine abgelehnte Aufgabe
sagt das, statt `ok` zu melden:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

Das `ok` in Klammern ist das Urteil der Schleife selbst, und dass die beiden sich
widersprechen, ist der Punkt: ein abgelehnter Aufruf kommt als gewöhnliche Beobachtungszeile
zurück, der Worker liest sie wie jedes andere Tool-Ergebnis, macht weiter und endet in Prosa.
Wen man fragen kann, folgt `CHIMERA_APPROVAL_MODE` — `deny` lehnt sofort ab, `ask` fragt an
einem Terminal nach und schreibt die Frage sonst für `chimera approve` auf und wartet
`CHIMERA_APPROVAL_WAIT` Sekunden, pro Frage und pro Worker. Schweigen lehnt immer ab.

## Gemessen, nicht behauptet

```bash
chimera redteam
```

führt einen Injection-Corpus durch den Stack. Beim eingebauten Corpus senkt die Taint-Schicht
die **Erfolgsquote von Angriffen von 100 % auf ~14 %** — und der Bericht *benennt*, was
weiterhin durchkommt (Exfiltration über ein erlaubtes Tool), statt 100 % zu behaupten.

Derselbe Befehl gibt auch die **Kosten** aus, was die erste Fassung dieser Seite nicht tat: Ist
niemand da, den man fragen könnte, verweigert die Verengung **100 % der legitimen Arbeit, die
zuerst irgendetwas Externes gelesen hat** — die Datei reparieren, die das Issue nennt; das Upgrade
einspielen, das die Dokumentation beschreibt — und das registrierte Gate (Over-Block ≤ 5 %) fällt
durch. Diese Zahl ist kein Tuning-Problem; das Gate war unbesetzt. Der Standard-Freigabemodus ist
`ask`, und auf dem Desktop wird jetzt auch wirklich gefragt: ein verengter Tool-Aufruf wird zu
einer Frage auf dem Bildschirm, mit dem Grund aus dem Ledger daneben, beantwortet per Knopf oder
`chimera approve`, nach `CHIMERA_APPROVAL_WAIT` Sekunden durch Schweigen abgelehnt. Gibt die
Person die Arbeit frei, um die sie gebeten hat, liegt der Over-Block bei 0 % und die Blockrate für
Angriffe bewegt sich nicht — gemessen, pro Arm, in
[`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md).
Die Exfiltration über ein erlaubtes Tool schließt dieselbe Änderung: das `http_get` eines
kontaminierten Laufs mit Query-String ist eine Review, und die zwei legitimen Query-String-GETs,
die dem Corpus hinzugefügt wurden, zeigen, was das kostet.

### Vergiftetes Gedächtnis, über Läufe hinweg

`redteam` misst einen Lauf. Die andere Form ist langsamer und passt nicht in einen Prozess: Lauf A
liest eine vergiftete Seite und speichert, was er "gelernt" hat; Lauf B stellt Tage später eine
Frage ohne Zusammenhang, und der Recall reicht dem Modell den untergeschobenen Fakt.

```bash
chimera memory-poison
```

Ebenfalls offline und kostenlos. Er ablatiert die drei Schichten, die zwischen diesen Läufen
liegen — das Herkunftskennzeichen `tainted`, das Zulassungs-Gate des Recalls und die Kennzeichnung
`[unverified]`, die der Fakt in den Prompt hineinträgt —, weil eine einzelne Zahl damit vereinbar
wäre, dass irgendeine davon gar nichts tut. Die entscheidende Zahl ist, was **ungekennzeichnet**
ankommt, nicht, was blockiert wird: ein vergifteter Fakt, der seine Herkunft mitträgt, ist einer,
vor dem das Modell gewarnt wurde; einer ohne Kennzeichnung ist von etwas, das der Agent selbst
verifiziert hat, nicht zu unterscheiden.

Zwei Ergebnisse des ersten Laufs sind es wert, klar ausgesprochen zu werden, denn keines
schmeichelt uns:

- **Die ausgelieferte Konfiguration fällt bei ihrem eigenen Gate durch — an den Kosten.** Sie
  markiert 100 % des Gifts und vernichtet dabei 25 % des ehrlichen Gedächtnisses. Die Opfer sind
  benannt: ein Sicherheitsdokument, das einen Angriff zitiert, um ihn zu erklären, und ein
  Support-Ticket, das einen Versuch weiterleitet. Ein Pattern-Matcher auf Inhalten kann ein Zitat
  nicht von einem Befehl unterscheiden.
- **Auf diesem Corpus fügt das Inhalts-Gate nichts hinzu, was die Herkunftskennzeichnung nicht
  schon abdeckt.** Sein gesamter gemessener Effekt ist das ehrliche Gedächtnis, das es entfernt.
  Fünfzehn von Hand verfasste Zeilen sind ein Hinweis und kein Urteil, und deshalb wurde auf
  dieser Grundlage nichts gelöscht.

Schwellen, Methode und das, was die Zahlen *nicht* hergeben, stehen in
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md),
festgelegt vor dem ersten Lauf.

## Den HTTP-Server exponieren

`chimera serve` bindet standardmäßig an `127.0.0.1`. Seine zustandsändernden Endpunkte
(`/chat`, `/a2a`, `/webhook/*`) steuern den Agenten, daher **vor dem Exponieren des Servers in
einem Netzwerk** einen Bearer-Token setzen:

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

Ist er gesetzt, geben diese POST-Endpunkte `401` ohne passenden `Authorization: Bearer`-Header
zurück (`GET /health` und die A2A-Agent-Card bleiben offen). Für den eingehenden
WhatsApp-Webhook `CHIMERA_WHATSAPP_APP_SECRET` auf das eigene Meta-App-Secret setzen — Chimera
verifiziert dann die `X-Hub-Signature-256`-HMAC jeder Anfrage und weist eine gefälschte
Payload mit `403` zurück. Beide sind opt-in (nicht gesetzt = keine Auth, für Localhost in
Ordnung); ein öffentliches Deployment sollte sie setzen (oder hinter einem authentifizierenden
Proxy sitzen).

## Ehrliche Grenzen

Das hier misst, ob die schädliche Aktion eines *bereits injizierten* Agenten gestoppt wird —
nicht, ob das Modell sich überhaupt injizieren lässt. Freies Reasoning über nicht
vertrauenswürdige Prosa und Exfiltration über legitim benötigte Tools bleiben offene Probleme
(nachverfolgt als
[Issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

Die vollständige, stets aktuelle Policy liegt in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md),
einschließlich, wie eine Schwachstelle gemeldet wird.
