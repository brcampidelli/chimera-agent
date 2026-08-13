---
source_sha256: 91c3e19b5cb75bc83cafd3408047b7f1df85f8cb28d2be4b66897ce7a2e6a1ba
---

# Externe Agenten (ACP)

Chimera kann einen Coding-Zug an einen Agenten übergeben, den es nicht geschrieben hat — Claude Code,
Gemini CLI oder jeden Adapter, der das [Agent Client Protocol](https://agentclientprotocol.com)
spricht. Transkript, Verifizierer, Sicherungskopie und Rückgängig bleiben Chimeras; die Arbeit ist
die eines anderen.

## Warum

Chimeras These war nie, dass seine Schleife die einzig gute ist. Es ist die Governance *um* eine
Schleife herum: das Kontaminationsregister, die Schreibregion, die Kopie vor dem Zug, das Urteil
danach, der Beleg, der sagt, was tatsächlich passiert ist. Das gilt für jeden Ausführenden. Sich zu
weigern, einen Ausführenden zu steuern, dem Sie bereits vertrauen, hieße auf der uninteressanteren
Hälfte des Produkts zu beharren.

## Was garantiert ist und was nicht

Lesen Sie diesen Teil vor der Installation — er entscheidet, ob diese Funktion für Sie richtig ist.

Ein ACP-Agent erklärt, welche Client-Fähigkeiten er nutzen wird, und Chimera bietet
`fs/read_text_file` und `fs/write_text_file` an. **Anbieten ist nicht Durchsetzen.** Die Agenten, die
es zu steuern lohnt, haben eigene Datei- und Shell-Werkzeuge: Claude Code schreibt über das Claude
Agent SDK und ist nicht verpflichtet, uns vorher zu fragen.

Konkret:

| | Chimeras eigene Schleife | Externer Agent |
|---|---|---|
| Schreibregion weist Schreibvorgänge außerhalb ab | Immer | Nur was über uns läuft |
| Shell läuft in der konfigurierten Sandbox | Immer | Der Agent führt auf seine Art aus |
| Kontaminationsregister schärft die Sperre | Immer | Nur bei Werkzeugen, die wir vermitteln |
| Momentaufnahme des Arbeitsordners vor dem Zug | Ja | **Ja** |
| Ganzen Zug mit einem Klick rückgängig machen | Ja | **Ja** |
| Jede erteilte Berechtigung steht im Beleg | — | **Ja** |

Die letzten drei Zeilen sind die echte Garantie, und sie sind das, was die Haltungszeile im
Code-Bildschirm verspricht, sobald ein externer Agent gewählt ist. Sie sagt nicht mehr „bearbeitet
innerhalb von `/projekt`, führt keine Befehle aus" — dieser Satz beschreibt Werkzeuge, die Chimera
besitzt — sondern dass eine Kopie gemacht wurde und der Zug rückgängig gemacht werden kann. Ein
Bildschirm, der den stärkeren Satz behielte, gäbe ein Versprechen ab, das der Zug nicht halten kann.

Chimera **lehnt** außerdem die Terminal-Fähigkeit von ACP ab. Ein von uns bereitgestelltes Terminal
wäre ein zweiter Ausführungsweg neben der Sandbox, ohne eine ihrer Regeln.

## Einrichtung

Für die Agenten, die Chimera kennt, ist nichts zu konfigurieren:

```bash
npm i -g @agentclientprotocol/claude-agent-acp   # Claude Code, benötigt Node 22+
npm i -g @google/gemini-cli                       # Gemini CLI (ACP-Modus ist upstream experimentell)
```

Prüfen Sie dann, was diese Maschine wirklich ausführen kann:

```bash
chimera doctor
```

`external_agents` meldet jeden mit `available: true/false` und, wenn falsch, die Zeile, die es
behebt. Die Verfügbarkeit wird auf der Maschine ermittelt, auf der der Sidecar läuft — bei einem
paketierten Desktop-Build eine von der CI zusammengebaute Maschine, die niemand angesehen hat. Also:
„das sollte da sein" ist kein Beleg.

Die Desktop-App zeigt über dem Eingabefeld eine Zeile **Wer arbeitet** mit dem, was `doctor` gefunden
hat. Ist nichts Lauffähiges installiert, erscheint die Zeile gar nicht; `doctor` ist der Ort für
„das haben Sie noch nicht, und so bekommen Sie es".

## Zugangsdaten

Jeder von Chimera gestartete Kindprozess bekommt eine Umgebung ohne `API_KEY` / `TOKEN` /
`SECRET`-Variablen, damit ein Shell-Befehl keinen Anbieterschlüssel ausgeben kann. Ein ACP-Agent ist
ein Programm, dessen ganze Aufgabe einen braucht, also erklärt jeder Agent **namentlich**, welche
Variablen er benötigt, und nur diese werden zurückgegeben:

- Claude Code: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`
- Gemini CLI: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`

Die ganze Umgebung durchzureichen wäre einfacher und gäbe jedem künftigen Adapter jeden Schlüssel auf
der Maschine.

## Ein eigener Adapter

Codex und andere erreichen ACP über Drittanbieter-Adapter, die dieses Projekt nicht ausgeführt hat.
Statt einen ungeprüften Befehl aufzulisten — was „wir haben es nicht geprüft" in „unterstützt"
verwandeln würde — richten Sie Chimera auf den, den Sie haben:

```jsonc
// POST /api/code/turn
{
  "message": "repariere den fehlschlagenden Test",
  "provider": "custom",
  "provider_command": "npx -y ein-acp-adapter --flag"
}
```

Der Befehl wird shell-artig zerlegt und **ohne** Shell ausgeführt, sodass eine versehentliche Pipe
ein Argument ist und kein zweiter Befehl. Unter Windows wird ein Argument mit cmd.exe-Syntax
(`& | < > ^ %`), das einen `.cmd`-Starter erreicht, abgelehnt statt maskiert: die Anführungsregeln
unterscheiden sich je Starter, und ein falscher Tipp führt Ihre Maschine aus statt eines Programms
darauf.

## Wie es funktioniert

- Ein Kindprozess pro **Unterhaltung**, nicht pro Zug. Ein `session/prompt` ist eine Nachricht in
  einem Kontext, den der Agent hält; ein frischer Prozess jedes Mal machte jeden Zug zum ersten Zug.
- Höchstens vier gleichzeitig, und einer, der eine Stunde unberührt bleibt, wird geschlossen. Jeder
  ist ein Prozess, der eine Modellverbindung hält.
- Der Prozess startet in einer eigenen Gruppe und wird als Baum beendet — ein Coding-Agent ist ein
  Starter, und nur den Prozess zu beenden, den wir halten, ließe die Arbeiter laufen und den Ordner
  gesperrt. Ein `atexit`-Reaper deckt den Fall ab, dass die App mitten im Zug beendet wird.
- Die `session/update`-Benachrichtigungen des Agenten werden in dieselben Ereignisse übersetzt, die
  die native Schleife ausgibt, sodass der Bildschirm keine zweite Implementierung braucht.
  Gedankenfragmente werden verworfen statt in die Antwort gefaltet; ein `diff`-Block wird zu dem
  vereinheitlichten Patch, den das Transkript ohnehin darstellt.
- Zahlen, die die native Schleife besitzt und diese nicht melden kann — `steps`,
  `context_peak_tokens` — kommen als `null` statt `0`. Null läse sich als „es hat nichts getan".

## Grenzen

- Berechtigungsanfragen werden mit `allow_once` beantwortet und **im Beleg festgehalten**. Eine
  Anfrage abzufangen, die der Agent gar nicht stellen musste, ist Theater; die ehrliche Variante ist
  gewähren, festhalten und sich auf die Sicherungskopie verlassen — die auch die Schreibvorgänge
  abdeckt, die nie gefragt haben.
- Fusion, Rollen, Gedächtnis und die Repository-Karte gehören Chimeras eigener Schleife. Ein externer
  Zug meldet `fused: false` und keine Gedächtnisnutzung, weil nichts davon stattgefunden hat.
- Geminis ACP-Modus ist upstream als experimentell markiert und kann sich zwischen Releases ändern.
