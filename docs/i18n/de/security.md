---
source_sha256: d4dc57476a5ef43343f96dfe46424b9883b2039ce6f92133c0f6917981bf8c70
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

## Die Stimme des Agenten in Prompts und Bot-Texten

Prompts und Bot-Vorlagen dürfen den Agenten keine Gefühle, Fürsorge, Freundschaft, Beziehung oder
Versprechen über sich selbst behaupten lassen. Sagen Sie, was er getan hat, was er nicht tun konnte und
wie sicher er ist. Kalibrierte Unsicherheit in der ersten Person (z. B. „Ich bin nicht sicher, aber…“)
ist erlaubt und nützlich.

## Die Schichten

- **Governance-Kernel** — jeder kontrollierte Tool-Aufruf ist allow / warn / review / block.
  Ein günstiger erster Filter für gefährliche Shell-Signaturen, nicht die Grenze.
- **Sandbox** — ein flüchtiger, netzwerkloser Container (`CHIMERA_SANDBOX=docker`), härtbar
  mit gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`). Der **verify-Befehl** läuft in derselben Sandbox
  wie die Shell des Agenten. Ein in `.chimera/verify.toml` festgelegter Verifier bleibt ein
  Shell-Befehl: Auf einem nicht isolierten Host läuft er nur, wenn für das Projekt ein aktiver
  `shell`-Grant besteht und `CHIMERA_HOST_EXEC` die Host-Ausführung nicht verweigert. Ohne diesen
  Grant enthält er sich; aus Build-Dateien abgeleitete Befehle durchlaufen weiterhin die
  `CHIMERA_HOST_EXEC`-Bestätigung und enthalten sich bei Ablehnung. Ein Projekt-Grant erlaubt den
  konfigurierten Befehl, macht die Host-Sandbox aber nicht isoliert (`CHIMERA_VERIFY_NETWORK=1`
  gibt einem Docker-Verifier Netzwerkzugriff; bei Kernel-Sandboxes ist das nicht möglich, daher
  läuft ein netzwerkabhängiger Verifier auf dem Host nur, wenn er anderweitig autorisiert ist).
  Behandeln Sie die Konfiguration und ihren Befehl als ausführbaren Projektinhalt; prüfen Sie sie,
  bevor Sie Shell-Zugriff gewähren. Ändert ein Turn den konfigurierten Verifier, enthält sich die
  Verifikation; der Befehl wird vor dem Turn erfasst und benötigt vor der ersten Ausführung eine
  dauerhafte, an seinen exakten SHA-256-Hash gebundene Zustimmung des Eigentümers (und erneut bei
  jeder Änderung). Diese Zustimmung bindet nur den Befehlsstring, nicht seine Auswirkungen:
  `pytest`, `make test` und `npm test` führen projektkontrollierte Dateien wie `conftest.py`,
  `Makefile`-Rezepte und Paket-Skripte aus, die der Agent ebenfalls geändert haben kann.
Ein optionaler Schlüssel `setup` in dieser Datei benennt einen Befehl zur Vorbereitung der Projektumgebung. Er benötigt eine eigene Zustimmung des Eigentümers, gebunden an den exakten Hash, sowie dieselbe aktive Projekt-Shell-Freigabe. Er wird höchstens einmal je Workspace und Befehlshash ausgeführt; Fehler werden dem Agenten gemeldet, ohne den Turn abzubrechen. Zustimmungen und die Markierung für die erfolgte Vorbereitung liegen unter Chimera’s Home, nicht im Checkout. Freigaben und genehmigte Hashes werden nur von dauerhaften Worktrees geerbt, die Chimera für dieses registrierte Projekt erstellt hat; beliebige Git-Worktrees erben sie nicht. Das Worktree-Register der App liegt unter `CHIMERA_HOME`. Ein Prozess mit bereits bestehendem Shell-Zugriff auf den Host kann dort schreiben, ebenso wie er das Projektregister bearbeiten kann.
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

### Ein Gespräch mit einer zweiten Person teilen

Die Desktop-App kann ein Code-Gespräch über ein **Token teilen, das nur dieses Gespräch öffnet** — nie
über das Server-Token. Der Besitzer stellt eines pro Gast aus (`POST /api/code/sessions/{id}/share`) und
kann es einzeln widerrufen; das Löschen des Gesprächs widerruft alle. Ein Gast erreicht vier Routen unter
`/guest/api/…`: das Gespräch lesen, live verfolgen, eine Nachricht hineinschicken, sehen, wer da ist.
Nichts anderes antwortet auf ein Freigabe-Token, und die Gast-App hat keine Route, um eine
Governance-Karte zu beantworten — die bleiben auf dem Bildschirm des Besitzers.

Die Gast-App ist auch das **Einzige**, was ausgeliefert wird, wenn der Besitzer die Netzwerktür öffnet
(`POST /api/code/share/network`, mit `DELETE` wieder geschlossen, beim Start nie offen): ein Rechner im LAN
erreicht diese vier Routen und nichts von dem, was das Server-Token schützt. Klar gesagt, bevor ein Link
weitergegeben wird: ein Gast kann den Agenten um alles bitten, worum der Besitzer ihn bitten würde, im
Projekt des Besitzers, mit dessen Werkzeugen und Ausgaben — unter den Standard-Nähten der Anfrage, und
ohne Karten freizugeben.

### Das eigene Handy oder der eigene Browser: ein Gerät koppeln

Ein Gast-Link ist für jemand anderen. Für den Besitzer auf dem eigenen Handy gibt es das Koppeln:
`chimera pair` (oder **Gerät koppeln** unter Einstellungen › Zugänge) gibt einen Code aus, der
**einmal, für fünf Minuten** gilt, mit einem QR-Code der Kopplungsadresse. Er wird in eine Seite
eingetippt, die ein zweiter LAN-Listener ausliefert — bei jedem Start geschlossen und nur vom
Besitzer geöffnet (`chimera pair --lan` oder **Öffnen** auf der Karte) — und wird zu einem
30-Tage-Cookie (`HttpOnly`, `SameSite=Strict`, `Secure` über https), signiert mit einem Schlüssel,
der aus dem Geräte-Geheimnis des Besitzers abgeleitet ist (`<home>/devices.key`). Fünf falsche Codes
entwerten jeden noch gültigen Code; fünf von einer Adresse sperren diese Adresse für fünfzehn
Minuten aus.

Ein gekoppeltes Gerät ist der Besitzer **zum Lesen und Sprechen**: Gespräche, Turns, Anleitung,
Antworten auf die eigenen Fragen des Agenten und das Ablehnen einer Freigabe. Es ist nicht der
Bildschirm des Besitzers. Freigeben verlangt weiterhin den Code jeder Frage (Studie 30, S30-30), und
die Routen, die Zugänge vergeben, Einstellungen ändern, Ordner freigeben, Bots starten oder den Dienst
stoppen, antworten einem Gerät mit `403`. Die Zugangskarte listet jedes Gerät mit letzter Nutzung und
Netzwerk (`192.168.1.x`); **Widerrufen** beendet eines sofort, auch einen offenen Live-Stream, und
**Alle Geräte widerrufen** ersetzt das Geheimnis, sodass kein vorher ausgestelltes Cookie mehr
verifiziert. Die Desktop-Brücke kann nicht koppeln, keinen Code ausstellen, nicht auflisten und nicht
widerrufen.

Unterwegs betreibt Chimera **kein eigenes Relay**. Wer den Geräte-Listener von außen erreichen will,
stellt einen Tunnel davor, den er selbst kontrolliert — [Tailscale](https://tailscale.com/) auf beiden
Geräten oder einen [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
zu `http://127.0.0.1:<port>` — und entscheidet das bewusst: er verlängert dieselbe Tür bis dorthin,
wohin der Tunnel reicht. Dafür nie die App selbst an `0.0.0.0` binden.

## Das Backend der Desktop-App

Das Backend der App lauscht auf Loopback, und Loopback ist keine Person: jeder Prozess auf dem
Rechner erreicht den Port.

- **Ein Token auf jeder Route.** Die Desktop-App startet ihr Backend immer mit einem Bearer-Token —
  einem neuen Geheimnis pro Start oder dem `CHIMERA_SERVER_TOKEN`, das der Besitzer bereits gesetzt
  hat. Jede `/api/…`-Route antwortet dann ohne es mit `401`, `/api/health` eingeschlossen. Offen
  bleiben nur die statischen Dateien der App und die Gast-App (`/guest/api/…`, die ihre eigenen
  Freigabe-Tokens prüft); die MCP-Brücke (`/api/bridge/…`) behält ihr eigenes Token. Die Shell
  übergibt das Token pro Start dem Backend in der Umgebung und dem Fenster über das
  Initialisierungsskript der Webview — nie auf einer Befehlszeile, in einer URL oder in einer Datei.
  Was das Backend startet, erbt es nicht: die Umgebung der Kindprozesse der Sandbox und die, mit der
  `gh` und `git` laufen, verwerfen jede Variable mit Geheimnis-Namen (ausgenommen `GH_TOKEN` und die
  übrigen eigenen Zugangsdaten von gh).
- **Freigeben ohne Code braucht den Bildschirm.** Ein Token sagt, wer die API aufrufen darf, nicht,
  dass der Besitzer hinsieht. Ein Ja ohne Code (`POST /api/approvals/{id}`) gelingt nur, wenn die
  Anfrage neben dem Bearer-Token auch das Bildschirm-Credential in `X-Chimera-Screen` trägt. Es wird
  bei jedem Start neu gezogen und im Speicher gehalten: nie in einer Datei, einem Eintrag, einem Log,
  der `.env` oder der Antwort einer Route, nie gleich dem Bearer-Token, und beim Lesen aus der
  Umgebung entfernt (die Shell übergibt es als `CHIMERA_SCREEN_TOKEN`). Jedes von der Platte gelesene
  Token — `service.token`, ein `CHIMERA_SERVER_TOKEN` in der `.env` — braucht für ein Ja weiterhin den
  Code pro Frage; ein Ablehnen braucht keinen Code. `chimera service` hält kein
  Bildschirm-Credential, dort verlangt also jedes Ja den Code. `chimera app` im Browser legt es in die
  Seite, die es einer Loopback-Anfrage ausliefert (`<meta name="chimera-screen">`), genau dort und dann,
  wo diese Seite bereits das Bearer-Token trägt — dieselbe Exposition, nach Entscheidung des Besitzers
  vom 16.10.2026.
- **Der Hintergrunddienst.** `chimera service start` führt dasselbe Backend ohne Fenster aus. Er
  veröffentlicht `~/.chimera/service-<channel>.json` (0600, atomar geschrieben; `CHIMERA_SERVICE_DIR`
  verlegt sie) mit dem dauerhaften Token des Besitzers (`<home>/service.token` oder ein vom Besitzer
  gewähltes `CHIMERA_SERVER_TOKEN`), und ein Client vertraut dieser Datei erst, nachdem
  `GET /api/info` mit derselben PID, Version, ID und demselben Datenordner antwortet. Dieses Token
  öffnet die ganze API, deshalb bleibt es von dem der Brücke getrennt: nichts auf der MCP-Seite liest
  die Dateien des Dienstes, und die Brücke verweigert sie als Dateien. Der Startereintrag der
  installierten App, `~/.chimera/desktop-install.json`, enthält nur vier Pfadvariablen —
  `CHIMERA_HOME`, `CHIMERA_WORKSPACE`, `CHIMERA_SHELL_PREFS`, `CHIMERA_SHELL_STATE` —, sein Schreiber
  verweigert jeden anderen Namen, und `start` prüft ihn, bevor es irgendetwas daraus ausführt.

### Volle Kontrolle über die Brücke

Die Desktop-Brücke (`chimera mcp desktop`) hat drei Schalter, jeder ohne den vorigen wirkungslos:
**Claude darf diese App bedienen**, **Volle Kontrolle** und seit der Entscheidung des Besitzers vom
2026-10-18 **Volle Kontrolle über die Brücke** unter Einstellungen > Sicherheit.

- **Nur der Bildschirm schaltet ein.** `CHIMERA_BRIDGE_FULL_CONTROL` steht nicht in der Liste der
  editierbaren Einstellungen: `PATCH /api/config` lehnt es für alle ab, und das `settings.edit` der
  Brücke lehnt es beim Namen ab. Die einzige Route, `POST /api/security/bridge-full-control`, lehnt jede
  von der Brücke weitergeleitete Anfrage ab und schaltet nur mit der Bildschirm-Berechtigung ein; das
  Token des Besitzers allein ist `service.token`, lesbar auf der Platte. Der Bildschirm fragt nach.
  Ausschalten braucht den Bildschirm nicht: es engt nur ein.
- **Was hinzukommt.** Die Entscheidungen des Besitzers (Befehle in einem Ordner erlauben, einen Agenten
  mit seinen Werkzeugen speichern, gemerkte Genehmigungsregeln, alles ablehnen, ein Projekt merken, auf
  einen Einstellungsvorschlag antworten, Plugins und Marktplätze, Worktree-Bereinigung), die Routen,
  die das `confirm: true` der Person verlangen (ein Design wiederherstellen, löschen und exportieren,
  die Speicherbereinigungen, Neustart und Zurücksetzen), die Einstellungen des Besitzers — Haltung,
  Schutzregeln, Genehmigungen, Obergrenzen, Modelle und Zeitplanung, geschrieben statt vorgeschlagen —
  und einen Bildschirm der App öffnen (`ui.navigate`, eine gegen die App-eigenen Bildschirme geprüfte
  Adresse, nie eine URL).
- **Was nie hinzukommt.** Zugangsdaten: kein Schlüssel, Token, Passwort oder OAuth-Geheimnis wird gelesen
  oder geschrieben; der Runner, einen MCP-Server hinzufügen oder sich dort anmelden bleiben beim
  Besitzer, und der Schlüsseltresor, das Lesen der `.env` durch den Agenten und die Basis-URL des
  Anbieters sind nicht schreibbar. Und wer die App erreicht: Geräte koppeln, Teilen, die LAN-Tür, das
  Server-Token, die Bots und ihre Zugangslisten.
- **Der Kernel entscheidet zuerst.** Eine Genehmigung beantwortet ein REVIEW; ein BLOCK des Kernels
  erreicht nie einen Genehmigenden. Die Prüfungen pro Lauf bleiben: ein von der Brücke gestarteter
  Lauf nutzt die konfigurierten Modelle und die konfigurierte Haltung.
- **Sichtbar und protokolliert.** Solange es an ist, zeigt jeder Bildschirm eine Leiste mit
  **Ausschalten** in einem Klick, und jeder Aufruf mit dieser Befugnis schreibt einen Audit-Eintrag
  (`via: "bridge"`, `scope: "full_control"`, Route, Status und IDs — nie ein Body oder Wert), der im Tab
  Sicherheit aufgelistet wird.
- **Ehrliche Grenze.** Der Schalter liegt in `.env`, wie die anderen zwei: ein Prozess, der diese Datei
  schreiben kann, schaltet ihn ohne Bildschirm ein. Die Werkzeuge des Agenten und die Brücke lehnen die
  Datei ab; alles andere, was als Besitzer läuft, nicht.

## Was der Besitzer entscheidet, und was ein Repository nicht kann

- **Bereitstellungsrichtlinie, nur verbietend.** `<CHIMERA_HOME>/policy.json` (oder die Datei, die
  `CHIMERA_POLICY_FILE` nennt) enthält geordnete Regeln `provider.use` / `integration.use` /
  `tool.use`; die letzte passende entscheidet. Ein `allow` dort gewährt nie etwas: es hebt nur ein
  früheres Deny derselben Datei auf, kann also weder `CHIMERA_TOOL_DENYLIST` noch den
  Reichweiten-Boden noch ein Kernel-BLOCK wieder öffnen. Eine ungültige Datei wird nie teilweise
  angewendet: `serve`, `chat`, `assist`, `tui` und `acp` verweigern den Start, `doctor` schlägt fehl,
  und der Desktop startet, verweigert aber jedes Werkzeug, bis sie korrigiert ist. Der Agent kann sie
  nicht umschreiben — die Schreibwerkzeuge verweigern die Datei, wo immer sie liegt, und die Brücke
  kann sie nicht umlenken.
- **„Immer in diesem Projekt“.** Auf einer Freigabekarte kann der Besitzer ein Ja für ein Projekt
  merken: das Werkzeug oder, bei einem Shell-Befehl, dessen erste zwei Wörter. Es beantwortet später
  passende Fragen, **auch nachdem der Lauf nicht vertrauenswürdige Inhalte gelesen hat** — die
  Entscheidung des Besitzers vom 15.10.2026, im Wissen, dass eine feindliche Seite einen Befehl
  innerhalb dieses Präfixes steuern könnte. Es überspringt nur die Frage: ein Kernel-BLOCK und der
  Reichweiten-Boden gewinnen weiterhin, und ein Befehl, der verkettet, pipet oder umleitet (`;`, `&&`,
  `|`, `>`, `$(`…), passt nie. Nur der Besitzer legt eines an oder widerruft es; die Brücke nicht, außer der
  Besitzer hat die volle Kontrolle über die Brücke eingeschaltet (oben).
- **Benannte Referenzen.** Ordner, die der Besitzer in `<CHIMERA_HOME>/references.json` auflistet,
  sind als `@alias/path` nur für die Lesewerkzeuge erreichbar (`read_file`, `list_dir`, `grep`,
  `glob`); jedes Schreibwerkzeug verweigert einen Alias. Eine Dateisystemwurzel, das Home des
  Benutzers oder der Datenordner wird als Referenz abgelehnt. Nur betreute Oberflächen erhalten sie —
  `chat`, `assist`, `tui`, ein betreutes `solve`, der Code-Turn des Desktops (nie der eines Gasts) —,
  Bots, Cron und `scenarios` haben also keine.
- **Agenten in Markdown.** `.chimera/agents/*.md` in einem Repository ist der Text eines anderen, kann
  also nur einschränken: seine Werkzeuge werden mit den governten Werkzeugen der Sitzung geschnitten,
  seine Schritte durch die der Sitzung begrenzt, unbekannte Schlüssel verweigert, sein `model`
  ignoriert, und sein Inhalt wird als nicht vertrauenswürdige Daten eingezäunt. Nur die Dateien des
  Besitzers in `<CHIMERA_HOME>/agents` dürfen ein Modell aus dem Katalog festlegen.
- **Mit Gesprächen verknüpfte Chats.** `/link` in Discord oder Telegram lässt einen Chat ein
  Code-Gespräch fortsetzen, und nur der in der Allowlist dieses Bots eingetragene Besitzer kann einen
  verknüpfen — ohne Allowlist niemand. Der Text eines Fremden in einem verknüpften Raum erreicht das
  Gespräch nie, und Medien werden abgelehnt statt weitergereicht. Der Turn läuft auf dem Boden des
  Bots, nicht dem des Desktops: nur die MCP-Server aus `CHIMERA_BOT_MCP_SERVERS`, keine
  OpenAPI-Konnektoren, weder Shell-Freigabe noch Host-Exec.
- **Die Konnektoren des Besitzers bleiben von Türen fern, die ein Fremder öffnen kann.** Ein
  OpenAPI-Konnektor ruft mit dem Schlüssel des Besitzers nach außen, antwortet also dem, der den Lauf
  steuert. Er lädt nicht auf einer Messaging-Oberfläche, solange irgendein konfigurierter Bot keine
  Allowlist hat, nie in einem GitHub-Issue-Job (jeder kann das Issue schreiben; geprüft wird nur das
  Repository), und auf `/v1/chat/completions` nur, solange `CHIMERA_SERVER_TOKEN` gesetzt ist. Ein
  Modul, `chimera/governance/surfaces.py`, entscheidet, welche Art Oberfläche ein Name ist, nach
  seiner Familie (`platform:discord` ist ein Bot); ein Test schlägt fehl, wenn ein Modul wieder seine
  eigene Liste schreibt.

## Ehrliche Grenzen

Das hier misst, ob die schädliche Aktion eines *bereits injizierten* Agenten gestoppt wird —
nicht, ob das Modell sich überhaupt injizieren lässt. Freies Reasoning über nicht
vertrauenswürdige Prosa und Exfiltration über legitim benötigte Tools bleiben offene Probleme
(nachverfolgt als
[Issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

Die vollständige, stets aktuelle Policy liegt in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md),
einschließlich, wie eine Schwachstelle gemeldet wird.
