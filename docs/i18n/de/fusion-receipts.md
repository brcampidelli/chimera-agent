---
source_sha256: 39b9206b1943aee5c2c508bd1f97b3c9bb931d3a4d75eb5dc24f861497cdfe04
---

# Fusion-Belege — "selektive Fusion mit Belegen"

Chimeras Reasoning-Kern mischt ein **Panel** von Modellen (Panel → Judge → Synthesizer). Fusion
bringt Qualität, kostet aber mehr Token, daher lautet die ehrliche Frage nie "ist Fusion gut?",
sondern "**hat es sich hier gelohnt?**". Belege beantworten das mit Zahlen statt mit einer
Behauptung.

Jeder Fusion-Lauf kann in einen **Beleg** eingepreist werden: was jeder Advisor (Panel-
Mitglied), der Judge und der Synthesizer gekostet haben — jeweils zum Tarif *seines eigenen*
Modells — plus ob der selektive Modus das Panel abgekürzt hat. Werden die Belege persistiert,
entsteht eine veröffentlichbare **Kosten-×-Qualität-Kurve**.

## Ausprobieren

```bash
# Show the itemized per-advisor cost of one run:
chimera fuse "Explain CAP theorem simply" --show-cost

# Append each run's receipt to a JSONL, then summarize the curve:
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` meldet die **Fusionsrate** (wie oft das volle Panel tatsächlich lief vs. eine
selektive Abkürzung), die mittleren/gesamten Kosten über die Läufe mit bekanntem Preis und —
wenn Belege ein Pass/Fail-Qualitätssignal tragen — die Erfolgsquote und die **Dollar pro
bestandener Antwort**.

## Ehrlichkeitsregeln (durch Konstruktion)

- **Token werden gemessen; Dollar werden geschätzt.** Die Token-Zählung kommt vom Provider; der
  Dollarbetrag wird zum ungefähren öffentlichen **Listenpreis** berechnet, ein Beleg ist also
  ein Schätzwert, keine Rechnung.
- **Unbekanntes Modell → unbekannte Kosten, nie null.** Läuft in irgendeiner Stufe ein Modell
  ohne hinterlegten Preis, ist die Gesamtsumme des Belegs `None` (`unknown`), sodass ein
  fehlender Preis sich nicht als "kostenlos" tarnen kann. Preise sind im Code überschreibbar
  (`chimera.fusion.set_price`).
- **Zuordnung pro Advisor.** Die Panel-Kosten werden *pro Modell* aufgeschlüsselt
  (`receipt.advisor_costs`), sodass sichtbar ist, welcher Advisor sich gelohnt hat — die
  Substanz hinter selektiver Fusion, kein Slogan.

## Warum es das gibt

Das Feld hat sich hin zu Routing/Kaskaden bewegt (mehr ausgeben nur, wenn der Einsatz es
rechtfertigt) und weg von dauerhaft aktiver Fusion. Belege sind das, was Chimera erlaubt,
**selektiv zu fusionieren und zu beweisen, dass es sich gelohnt hat** — die Kosten-×-Qualität-
Kurve ist der Beleg, veröffentlicht einschließlich der Läufe, in denen Fusion *nicht* geholfen
hat.
