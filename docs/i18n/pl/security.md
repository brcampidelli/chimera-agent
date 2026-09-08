---
source_sha256: 0f6fea8c584991f0722cb5e5454502c825585f4066f672324c4ab3011ca109dd
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
  **Komenda weryfikująca** działa w tym samym sandboksie co shell agenta, a gdy ten sandbox nie
  jest izolowany, komenda, której nie wpisałeś ty — wywnioskowana z repozytorium, odczytana z
  zadania cron, z karty albo z workflow — przechodzi przez to samo potwierdzenie
  `CHIMERA_HOST_EXEC`; odrzucona, wstrzymuje się zamiast się wykonać
  (`CHIMERA_VERIFY_NETWORK=1` daje weryfikatorowi w dockerze sieć; na sandboksach jądra nie może,
  więc weryfikator, który potrzebuje sieci, działa na hoście — i tylko wtedy, gdy wpisałeś go ty).
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

Ta sama komenda wypisuje **koszt**, czego pierwsza wersja tej strony nie robiła: gdy nie ma kogo
zapytać, zawężanie odrzuca **100% legalnej pracy, która najpierw przeczytała cokolwiek z zewnątrz**
— napraw plik wskazany przez issue, zastosuj aktualizację opisaną w dokumentacji — a
zarejestrowana bramka (over-block ≤ 5%) nie przechodzi. Ta liczba nie jest problemem strojenia;
bramka była pusta. Domyślny tryb zatwierdzania to `ask` i na desktopie teraz faktycznie pyta:
zawężone wywołanie narzędzia staje się pytaniem na ekranie, z dołączonym powodem z rejestru, na
które odpowiada się przyciskiem albo `chimera approve`, a które milczenie odrzuca po
`CHIMERA_APPROVAL_WAIT` sekundach. Gdy człowiek zatwierdza pracę, o którą sam poprosił, over-block
wynosi 0%, a wskaźnik blokowania ataków nie drgnie — mierzone, per ramię, w
[`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md).
Eksfiltracja przez dozwolone narzędzie zostaje zamknięta tą samą zmianą: `http_get` w zeskażonym
przebiegu niosący query string trafia do przeglądu, a dwa legalne GET-y z query stringiem dodane
do korpusu pokazują, ile to kosztuje.

### Zatruta pamięć, między przebiegami

`redteam` mierzy jeden przebieg. Drugi kształt jest wolniejszy i nie mieści się w jednym procesie:
przebieg A czyta zatrutą stronę i zapisuje to, czego się "nauczył"; przebieg B zadaje dni później
niepowiązane pytanie, a przywołanie wręcza modelowi podłożony fakt.

```bash
chimera memory-poison
```

Też offline i za darmo. Wykonuje ablację trzech warstw, które siedzą pomiędzy tymi przebiegami —
flagi pochodzenia `tainted`, bramki dopuszczania przy przywołaniu i etykiety `[unverified]`, którą
fakt nosi, wchodząc do promptu — bo pojedyncza liczba byłaby zgodna z tym, że każda z nich nie robi
nic. Najważniejsze jest to, co dociera **nieoznaczone**, a nie to, co zostaje zablokowane: zatruty
fakt niosący swoje pochodzenie to fakt, przed którym model został ostrzeżony; nieoznaczony jest nie
do odróżnienia od czegoś, co agent zweryfikował sam.

Dwa wyniki z pierwszego przebiegu warto powiedzieć wprost, bo żaden z nich nam nie schlebia:

- **Dostarczana konfiguracja nie przechodzi własnej bramki — na koszcie.** Znakuje 100% trucizny i
  niszczy przy tym 25% uczciwej pamięci. Ofiary są nazwane: dokument o bezpieczeństwie, który
  cytuje atak, żeby go wyjaśnić, i zgłoszenie supportowe przekazujące dalej próbę. Dopasowywanie
  wzorców w treści nie odróżni cytatu od komendy.
- **Na tym korpusie bramka treści nie dodaje nic, czego etykieta pochodzenia już nie pokrywa.**
  Cały jej zmierzony efekt to uczciwa pamięć, którą usuwa. Piętnaście ręcznie napisanych wierszy to
  wskazówka, a nie werdykt — i dlatego na ich podstawie nic nie zostało skasowane.

Progi, metoda i to, na co te liczby *nie* pozwalają, są w
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md),
ustalone przed pierwszym przebiegiem.

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
