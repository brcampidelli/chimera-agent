---
source_sha256: 39b9206b1943aee5c2c508bd1f97b3c9bb931d3a4d75eb5dc24f861497cdfe04
---

# Fusion receipts — "fusione selettiva con ricevute"

Il nucleo di ragionamento di Chimera fonde un **panel** di modelli (panel → giudice →
sintetizzatore). La fusione compra qualità ma costa più token, quindi la domanda onesta non è mai
"la fusione è buona?", ma "**ne è valsa la pena, qui?**". Le ricevute rispondono a questo con
numeri invece che con un'affermazione.

Ogni esecuzione di fusione può essere prezzata in una **ricevuta**: quanto è costato ogni advisor
(membro del panel), il giudice e il sintetizzatore — ciascuno alla tariffa del *proprio* modello —
più se la modalità selettiva ha interrotto il panel in anticipo. Persisti le ricevute e ottieni una
**curva costo × qualità** pubblicabile.

## Provalo

```bash
# Show the itemized per-advisor cost of one run:
chimera fuse "Explain CAP theorem simply" --show-cost

# Append each run's receipt to a JSONL, then summarize the curve:
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` riporta il **fusion rate** (con quale frequenza il panel completo è effettivamente
girato rispetto a un'interruzione selettiva), il costo medio/totale sulle esecuzioni che avevano un
prezzo noto e — quando le ricevute portano un segnale di qualità pass/fail — il tasso di successo e
i **dollari per risposta riuscita**.

## Regole di onestà (per costruzione)

- **I token sono misurati; i dollari sono stimati.** I conteggi dei token arrivano dal provider; la
  cifra in dollari è calcolata al **prezzo di listino** pubblico approssimativo, quindi una ricevuta
  è uno stimatore, non una fattura.
- **Modello sconosciuto → costo sconosciuto, mai zero.** Se una qualsiasi fase esegue un modello
  senza un prezzo registrato, il totale della ricevuta è `None` (`unknown`), così un prezzo mancante
  non può mascherarsi da "gratis". I prezzi sono sovrascrivibili nel codice
  (`chimera.fusion.set_price`).
- **Attribuzione per advisor.** Il costo del panel è scomposto *per modello*
  (`receipt.advisor_costs`), così puoi vedere quale advisor si è guadagnato il posto — la sostanza
  dietro la fusione selettiva, non uno slogan.

## Perché esiste

Il campo si è spostato verso routing/cascate (spendere di più solo quando la posta in gioco lo
giustifica), allontanandosi dalla fusione sempre attiva. Le ricevute sono ciò che permette a
Chimera di fondere **selettivamente e dimostrare che ne è valsa la pena** — la curva costo×qualità
è la prova, pubblicata includendo anche le esecuzioni in cui la fusione *non* ha aiutato.
