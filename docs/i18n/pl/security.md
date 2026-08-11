---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
---

# Bezpieczeństwo i zabezpieczenia

Chimera potrafi uruchamiać komendy shell, edytować pliki, wywoływać API i modyfikować własne
skille. Dostarcza **obronę wielowarstwową** (defense-in-depth), i — to ważne — dokumentacja
mówi wprost, gdzie kończy się każda warstwa.

!!! warning "Jedna zasada"
    Żadne z tych zabezpieczeń nie zastępuje **uruchomienia w izolowanym środowisku**, gdy
    przyznajesz autonomię. Domyślny runner `local` nie jest izolowany; użyj
    `CHIMERA_SANDBOX=docker` (sieć wyłączona, opcjonalnie pod gVisor) do niezaufanej pracy.

## Warstwy

- **Jądro governance** — każde bramkowane wywołanie narzędzia to allow / warn / review / block.
  Tani pierwszy filtr niebezpiecznych sygnatur shellowych, nie granica.
- **Sandbox** — efemeryczny kontener z wyłączoną siecią (`CHIMERA_SANDBOX=docker`), możliwy do
  wzmocnienia gVisorem (`CHIMERA_SANDBOX_RUNTIME=runsc`).
- **Allowlista narzędzi na sesję** — przyznaje danemu przebiegowi tylko narzędzia, których
  potrzebuje; reszta jest całkowicie usuwana ze schematu modelu.
- **Śledzenie skażenia** (`--taint`) — niezaufana treść jest ogrodzona jako dane, jej pochodzenie
  podąża za nią do pamięci i skilli (skill z zeskażonego przebiegu jest trzymany do przeglądu),
  a gdy przebieg zostanie zeskażony, niebezpieczne narzędzia się zawężają.
- **Kwarantannowany czytnik** — wzorzec dual-LLM / CaMeL: niezaufana treść jest czytana przez
  model bez narzędzi, który może wyemitować tylko pola zwalidowane wg schematu, więc injection
  nie może wyprodukować nowej instrukcji ani wywołania narzędzia.
- **Monitor między-agentowy** — przy fan-out, monitor na jednego workera jest ślepy na
  *rozdzielony* przepływ (jeden worker pobiera niezaufaną treść, inny worker ją zatapia (sink) —
  pobranie i zatopienie żyją w oddzielnych rejestrach). Monitor zbiorczy widzi cały fan-out; jest
  **zawsze włączony** dla `solve-batch` / `crew-isolated`.

## Fan-out: monitor między-agentowy

Gdy kilku workerów używających narzędzi działa równolegle (`solve-batch`, `crew-isolated`),
każdy dostaje własny rejestr uprawnień (capability ledger), a po zakończeniu wsadu monitor
zbiorczy przebiega przez wszystkie z nich. Wychwytuje wzorce, których żaden pojedynczy monitor
workera nie widzi — rozdzieloną eksfiltrację, gdzie worker A pobiera niezaufaną treść, a worker B
ją wykonuje lub eksfiltruje:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

Zawsze tylko **eskaluje do przeglądu** — nigdy nie blokuje przebiegu — i jest czystą
obserwowalnością (rejestrowanie, żadna zmiana zachowania). Dodaj `--taint` na wierzchu, by
dodatkowo uzbroić adaptacyjną allowlistę każdego workera (narzędzia niebezpieczne-gdy-zeskażone
wymagają wtedy zatwierdzenia).

## Mierzone, nie deklarowane

```bash
chimera redteam
```

przepuszcza korpus injection przez cały stos. Na wbudowanym korpusie warstwa skażenia tnie
**wskaźnik skuteczności ataku ze 100% do ~14%** — a raport *nazywa*, co nadal się przebija
(eksfiltracja przez dozwolone narzędzie), zamiast rościć sobie 100%.

## Wystawianie serwera HTTP

`chimera serve` domyślnie wiąże się z `127.0.0.1`. Jego endpointy zmieniające stan (`/chat`,
`/a2a`, `/webhook/*`) sterują agentem, więc **zanim wystawisz serwer do sieci**, ustaw token
bearer:

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

Gdy jest ustawiony, te endpointy POST zwracają `401` bez pasującego nagłówka
`Authorization: Bearer` (`GET /health` i karta agenta A2A pozostają otwarte). Dla przychodzącego
webhooka WhatsApp, ustaw `CHIMERA_WHATSAPP_APP_SECRET` na sekret twojej aplikacji Meta — Chimera
wtedy weryfikuje HMAC `X-Hub-Signature-256` każdego żądania i odrzuca sfałszowany payload z
`403`. Oba są opt-in (nieustawione = brak autoryzacji, w porządku dla localhost); wdrożenie
publiczne powinno je ustawić (albo siedzieć za uwierzytelniającym proxy).

## Uczciwe granice

To mierzy, czy szkodliwe działanie *już zainfekowanego* agenta zostaje zatrzymane — nie czy
model można w ogóle zainfekować. Swobodne rozumowanie nad niezaufaną prozą i eksfiltracja przez
zasadnie potrzebne narzędzia pozostają otwartymi problemami (śledzone jako
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

Pełna, zawsze aktualna polityka żyje w
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), łącznie z
tym, jak zgłosić lukę.
