---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
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

## Gemessen, nicht behauptet

```bash
chimera redteam
```

führt einen Injection-Corpus durch den Stack. Beim eingebauten Corpus senkt die Taint-Schicht
die **Erfolgsquote von Angriffen von 100 % auf ~14 %** — und der Bericht *benennt*, was
weiterhin durchkommt (Exfiltration über ein erlaubtes Tool), statt 100 % zu behaupten.

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
