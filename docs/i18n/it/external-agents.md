---
source_sha256: 91c3e19b5cb75bc83cafd3408047b7f1df85f8cb28d2be4b66897ce7a2e6a1ba
---

# Agenti esterni (ACP)

Chimera può affidare un turno di codice a un agente che non ha scritto — Claude Code, Gemini CLI, o
qualsiasi adattatore che parli l'[Agent Client Protocol](https://agentclientprotocol.com). La
trascrizione, il verificatore, la copia di sicurezza e l'annullamento restano di Chimera; il lavoro è
di un altro.

## Perché

La tesi di Chimera non è mai stata che il suo ciclo sia l'unico buono. È la governance *attorno* a un
ciclo: il registro di contaminazione, la regione di scrittura, la copia prima del turno, il verdetto
dopo, la ricevuta che dice cosa è successo davvero. Vale per qualunque esecutore. Rifiutarsi di
guidare un esecutore di cui ti fidi già significherebbe insistere sulla metà meno interessante del
prodotto.

## Cosa è garantito e cosa no

Leggi questa parte prima dell'installazione: è quella che decide se questa funzione fa per te.

Un agente ACP dichiara quali capacità del client userà, e Chimera offre `fs/read_text_file` e
`fs/write_text_file`. **Offrire non è imporre.** Gli agenti che vale la pena guidare hanno strumenti
propri per file e terminale: Claude Code scrive tramite il Claude Agent SDK e non ha alcun obbligo di
chiedercelo prima.

In concreto:

| | Ciclo proprio di Chimera | Agente esterno |
|---|---|---|
| La regione di scrittura rifiuta fuori da sé | Sempre | Solo ciò che passa da noi |
| La shell gira nella sandbox configurata | Sempre | L'agente esegue a modo suo |
| Il registro di contaminazione arma il blocco | Sempre | Solo per gli strumenti che mediamo |
| Istantanea della cartella prima del turno | Sì | **Sì** |
| Annullare l'intero turno con un clic | Sì | **Sì** |
| Ogni permesso concesso finisce in ricevuta | — | **Sì** |

Le ultime tre righe sono la garanzia vera, ed è ciò che promette la riga di postura nella schermata
Codice quando è selezionato un agente esterno. Smette di dire «modifica dentro `/progetto`, non
esegue comandi» — quella frase descrive strumenti che Chimera possiede — e dice invece che è stata
presa una copia e che il turno si può annullare. Una schermata che tenesse la frase più forte
farebbe una promessa che il turno non può mantenere.

Chimera inoltre **rifiuta** la capacità terminale di ACP. Un terminale ospitato da noi sarebbe una
seconda via di esecuzione accanto alla sandbox, senza nessuna delle sue regole.

## Installazione

Nulla da configurare per gli agenti che Chimera conosce:

```bash
npm i -g @agentclientprotocol/claude-agent-acp   # Claude Code, richiede Node 22+
npm i -g @google/gemini-cli                       # Gemini CLI (la modalità ACP è sperimentale a monte)
```

Poi controlla cosa questa macchina riesce davvero a eseguire:

```bash
chimera doctor
```

`external_agents` riporta ciascuno con `available: true/false` e, quando è falso, la riga che lo
risolve. La disponibilità è risolta sulla macchina dove gira il sidecar — che, in una build desktop
pacchettizzata, è una macchina assemblata dalla CI che nessuno ha guardato. Cioè: «dovrebbe esserci»
non è una prova.

L'app desktop mostra una riga **Chi esegue** sopra il compositore con quanto trovato da `doctor`.
Quando non c'è nulla di eseguibile installato, la riga non compare; `doctor` è il posto giusto per
«questo non ce l'hai ancora, ecco come si ottiene».

## Credenziali

Ogni processo figlio lanciato da Chimera riceve un ambiente privato delle variabili `API_KEY` /
`TOKEN` / `SECRET`, così un comando di shell non può stampare una chiave del provider. Un agente ACP
è un programma il cui intero lavoro ne richiede una, quindi ogni agente dichiara **per nome** le
variabili che gli servono, e solo quelle vengono rimesse:

- Claude Code: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`
- Gemini CLI: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`

Passare l'intero ambiente sarebbe più facile e consegnerebbe a ogni futuro adattatore ogni chiave
presente sulla macchina.

## Un adattatore tuo

Codex e altri raggiungono ACP tramite adattatori di terze parti che questo progetto non ha eseguito.
Invece di elencare un comando non verificato — il che trasformerebbe «non abbiamo controllato» in
«supportato» — punta Chimera su quello che hai:

```jsonc
// POST /api/code/turn
{
  "message": "sistema il test che fallisce",
  "provider": "custom",
  "provider_command": "npx -y un-adattatore-acp --flag"
}
```

Il comando viene diviso in stile shell ed eseguito **senza** shell, così una pipe di troppo è un
argomento e non un secondo comando. Su Windows, un argomento con sintassi cmd.exe (`& | < > ^ %`) che
raggiunge un launcher `.cmd` viene rifiutato anziché sottoposto a escape: le regole di quoting
cambiano da launcher a launcher, e un'ipotesi sbagliata esegue la tua macchina invece di un programma
su di essa.

## Come funziona

- Un processo figlio per **conversazione**, non per turno. Un `session/prompt` è un messaggio dentro
  un contesto che l'agente conserva; un processo nuovo ogni volta renderebbe ogni turno il primo.
- Al massimo quattro vivi insieme, e uno lasciato fermo per un'ora viene chiuso. Ciascuno è un
  processo che tiene una connessione al modello.
- Il processo nasce nel proprio gruppo e viene ucciso ad albero — un agente di codice è un launcher, e
  uccidere solo il processo che teniamo lascerebbe i lavoratori in esecuzione e la cartella bloccata.
  Un reaper su `atexit` copre il caso dell'app chiusa a metà turno.
- Le notifiche `session/update` dell'agente sono tradotte negli stessi eventi che emette il ciclo
  nativo, così la schermata non ha bisogno di una seconda implementazione. I frammenti di
  ragionamento vengono scartati anziché fusi nella risposta; un blocco `diff` diventa la patch
  unificata che la trascrizione già mostra.
- I numeri che il ciclo nativo possiede e questo non può riportare — `steps`, `context_peak_tokens` —
  arrivano come `null` e non come `0`. Zero si leggerebbe come «non ha fatto nulla».

## Limiti

- Le richieste di permesso ricevono `allow_once` e vengono **registrate in ricevuta**. Filtrare una
  richiesta che l'agente non era obbligato a fare è teatro; la versione onesta è concedere,
  registrare e affidarsi alla copia di sicurezza — che copre anche le scritture che non hanno mai
  chiesto.
- Fusione, ruoli, memoria e mappa del repository appartengono al ciclo proprio di Chimera. Un turno
  esterno riporta `fused: false` e nessun uso di memoria perché niente di tutto ciò è avvenuto.
- La modalità ACP di Gemini è marcata come sperimentale a monte e il suo comportamento può cambiare
  tra le versioni.
