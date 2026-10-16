---
source_sha256: 47d00ef56102a6d9b778d54536495c58bb5edc5fa6e6c6721c6d4065231e8b38
---

# Bezpieczeństwo i zabezpieczenia

Chimera potrafi uruchamiać komendy shell, edytować pliki, wywoływać API i modyfikować własne
skille. Dostarcza **obronę wielowarstwową** (defense-in-depth), i — to ważne — dokumentacja
mówi wprost, gdzie kończy się każda warstwa.

!!! warning "Jedna zasada"
    Żadne z tych zabezpieczeń nie zastępuje **uruchomienia w izolowanym środowisku**, gdy
    przyznajesz autonomię. Domyślny runner `local` nie jest izolowany; użyj
    `CHIMERA_SANDBOX=docker` (sieć wyłączona, opcjonalnie pod gVisor) do niezaufanej pracy.

## Głos agenta w promptach i tekstach bota

Prompty i szablony bota nie mogą sprawiać, że agent przypisuje sobie uczucia, troskę, przyjaźń, relację
ani obietnice. Mów, co zrobił, czego nie mógł zrobić i na ile jest pewny. Skalibrowana niepewność
w pierwszej osobie (np. „nie jestem pewien, ale…”) jest dozwolona i przydatna.

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

**Zatwierdzanie i jak wygląda odrzucony worker.** Każdy worker ma własnego zatwierdzającego i
własny zapis tego, na co mu pozwolono, więc odrzucone zadanie mówi to wprost, zamiast
raportować `ok`:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

`ok` w nawiasie to werdykt samej pętli, a to, że oba się nie zgadzają, jest tu sednem:
odrzucone wywołanie wraca jako zwykła linia obserwacji, więc worker czyta ją jak każdy wynik
narzędzia, idzie dalej i kończy prozą. Kogo można zapytać, decyduje `CHIMERA_APPROVAL_MODE` —
`deny` odmawia od razu, `ask` pyta na terminalu, a poza nim zapisuje pytanie dla
`chimera approve` i czeka `CHIMERA_APPROVAL_WAIT` sekund, na pytanie i na workera. Milczenie
zawsze odmawia.

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

### Udostępnianie rozmowy drugiej osobie

Aplikacja desktopowa może udostępnić rozmowę o kodzie przez **token otwierający tylko tę rozmowę** —
nigdy przez token serwera. Właściciel wystawia po jednym na gościa (`POST /api/code/sessions/{id}/share`)
i może go unieważnić osobno; usunięcie rozmowy unieważnia wszystkie. Gość dociera do czterech tras pod
`/guest/api/…`: odczytać rozmowę, śledzić ją na żywo, wysłać do niej wiadomość, zobaczyć kto jest.
Nic więcej nie odpowiada na token udostępnienia, a aplikacja gościa nie ma trasy, którą można by
odpowiedzieć na kartę nadzoru — te zostają na ekranie właściciela.

Aplikacja gościa jest też **jedyną** rzeczą serwowaną, gdy właściciel otwiera drzwi sieciowe
(`POST /api/code/share/network`, zamykane przez `DELETE`, nigdy otwarte przy starcie): maszyna w LAN
dociera do tych czterech tras i do niczego, co chroni token serwera. Powiedziane wprost przed wręczeniem
linku: gość może poprosić agenta o wszystko, o co poprosiłby właściciel, w projekcie właściciela, jego
narzędziami i za jego pieniądze — w domyślnych ramach żądania, i bez zatwierdzania kart.

### Twój własny telefon lub przeglądarka: parowanie urządzenia

Link gościa jest dla kogoś innego. Dla właściciela na jego własnym telefonie jest parowanie:
`chimera pair` (albo **Sparuj urządzenie** w Ustawienia › Dostęp) wypisuje kod działający
**jednorazowo, przez pięć minut**, z kodem QR adresu parowania. Wpisuje się go na stronie serwowanej
przez drugi listener w LAN — zamknięty przy każdym starcie i otwierany tylko przez właściciela
(`chimera pair --lan` albo **Otwórz** na karcie) — i staje się 30-dniowym ciasteczkiem (`HttpOnly`,
`SameSite=Strict`, `Secure` przez https) podpisanym kluczem wyprowadzonym z sekretu urządzeń
właściciela (`<home>/devices.key`). Pięć błędnych kodów unieważnia każdy ważny kod; pięć z jednego
adresu blokuje ten adres na piętnaście minut.

Sparowane urządzenie jest właścicielem **do czytania i rozmowy**: rozmowy, tury, wskazówki,
odpowiedzi na własne pytania agenta i odrzucenie zatwierdzenia. Nie jest ekranem właściciela.
Zatwierdzanie nadal wymaga kodu każdego pytania (badanie 30, S30-30), a trasy, które wydają dostęp,
zmieniają ustawienia, przyznają foldery, uruchamiają boty albo zatrzymują usługę, odpowiadają
urządzeniu `403`. Karta dostępu wymienia każde urządzenie z ostatnim użyciem i siecią
(`192.168.1.x`); **Unieważnij** kończy jedno od razu, łącznie z otwartym przez nie strumieniem na
żywo, a **Unieważnij wszystkie urządzenia** wymienia sekret, więc żadne wcześniej wydane ciasteczko
już się nie weryfikuje. Most aplikacji desktopowej nie może parować, wystawić kodu, wyświetlić listy
ani unieważniać.

Poza domem Chimera **nie uruchamia własnego przekaźnika**. Jeśli chcesz dotrzeć do listenera
urządzeń z zewnątrz, postaw przed nim tunel, który kontrolujesz — [Tailscale](https://tailscale.com/)
na obu urządzeniach albo [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
do `http://127.0.0.1:<port>` — i zdecyduj o tym świadomie: przedłuża te same drzwi wszędzie, dokąd
sięga tunel. Nigdy nie wiąż w tym celu samej aplikacji z `0.0.0.0`.

## Backend aplikacji desktopowej

Backend aplikacji nasłuchuje na loopbacku, a loopback to nie osoba: do portu dociera każdy proces na
maszynie.

- **Token na każdej trasie.** Aplikacja desktopowa zawsze uruchamia swój backend z tokenem bearer —
  nowym sekretem przy każdym starcie albo z `CHIMERA_SERVER_TOKEN`, który właściciel już ustawił.
  Każda trasa `/api/…` odpowiada wtedy bez niego `401`, łącznie z `/api/health`. Otwarte zostają tylko
  statyczne pliki aplikacji i aplikacja gościa (`/guest/api/…`, która sprawdza własne tokeny
  udostępnienia); most MCP (`/api/bridge/…`) zachowuje własny token. Powłoka przekazuje token startu
  backendowi w środowisku, a oknu przez skrypt inicjalizacyjny webview — nigdy w linii poleceń, w URL
  ani w pliku. To, co backend uruchamia, go nie dziedziczy: środowisko procesów potomnych sandboxa i
  to, z którym działają `gh` i `git`, odrzuca każdą zmienną o nazwie sekretu (poza `GH_TOKEN` i
  pozostałymi własnymi poświadczeniami gh).
- **Zatwierdzenie bez kodu wymaga ekranu.** Token mówi, kto może wywołać API, a nie że właściciel
  patrzy. „Tak” bez kodu (`POST /api/approvals/{id}`) przechodzi tylko wtedy, gdy żądanie niesie
  obok tokenu bearer także poświadczenie ekranu w `X-Chimera-Screen`. Jest losowane przy każdym
  starcie i trzymane w pamięci: nigdy w pliku, rekordzie, logu, `.env` ani w odpowiedzi trasy, nigdy
  równe tokenowi bearer, i usuwane ze środowiska przy odczycie (powłoka przekazuje je jako
  `CHIMERA_SCREEN_TOKEN`). Każdy token odczytany z dysku — `service.token`, `CHIMERA_SERVER_TOKEN` w
  `.env` — nadal potrzebuje kodu danego pytania, by powiedzieć „tak”; odmowa nie wymaga kodu.
  `chimera service` nie ma poświadczenia ekranu, więc tam każde „tak” wymaga kodu. `chimera app` w
  przeglądarce umieszcza je w stronie serwowanej na żądanie z loopbacku
  (`<meta name="chimera-screen">`), dokładnie tam i wtedy, gdzie ta strona już niesie token bearer —
  ta sama ekspozycja, decyzją właściciela z 16.10.2026.
- **Usługa w tle.** `chimera service start` uruchamia ten sam backend bez okna. Publikuje
  `~/.chimera/service-<channel>.json` (0600, zapisywany atomowo; `CHIMERA_SERVICE_DIR` go przenosi) z
  trwałym tokenem właściciela (`<home>/service.token` albo wybrany przez właściciela
  `CHIMERA_SERVER_TOKEN`), a klient ufa temu plikowi dopiero, gdy `GET /api/info` odpowie tym samym
  pid, wersją, id i folderem danych. Ten token otwiera całe API, więc trzymany jest osobno od tokenu
  mostu: nic po stronie MCP nie czyta plików usługi, a most odmawia ich jako plików. Rekord
  uruchomieniowy zainstalowanej aplikacji, `~/.chimera/desktop-install.json`, zawiera tylko cztery
  zmienne ścieżek — `CHIMERA_HOME`, `CHIMERA_WORKSPACE`, `CHIMERA_SHELL_PREFS`,
  `CHIMERA_SHELL_STATE` — jego zapis odrzuca każdą inną nazwę, a `start` waliduje go, zanim cokolwiek
  na jego podstawie uruchomi.

## Co decyduje właściciel, a czego nie może repozytorium

- **Polityka wdrożenia, tylko zakazująca.** `<CHIMERA_HOME>/policy.json` (albo plik wskazany przez
  `CHIMERA_POLICY_FILE`) zawiera uporządkowane reguły `provider.use` / `integration.use` / `tool.use`;
  decyduje ostatnia pasująca. `allow` nigdy niczego tam nie przyznaje: tylko cofa wcześniejszy deny z
  tego samego pliku, więc nie może ponownie otworzyć `CHIMERA_TOOL_DENYLIST`, podłogi zasięgu ani
  BLOCK jądra. Nieprawidłowy plik nigdy nie jest stosowany częściowo: `serve`, `chat`, `assist`, `tui` i
  `acp` odmawiają startu, `doctor` zawodzi, a desktop startuje, ale odmawia każdego narzędzia, dopóki
  plik nie zostanie poprawiony. Agent nie może go przepisać — narzędzia zapisu odmawiają tego pliku,
  gdziekolwiek leży, a most nie może go przekierować.
- **„Zawsze w tym projekcie”.** Na karcie zatwierdzenia właściciel może zapamiętać „tak” dla projektu:
  narzędzie albo, dla polecenia powłoki, jego dwa pierwsze słowa. Odpowiada ono potem na pasujące
  pytania, **także po tym, jak przebieg przeczytał niezaufaną treść** — decyzja właściciela z
  15.10.2026, podjęta ze świadomością, że wroga strona mogłaby pokierować poleceniem w obrębie tego
  prefiksu. Pomija ono tylko pytanie: BLOCK jądra i podłoga zasięgu nadal wygrywają, a polecenie,
  które łączy, używa potoku albo przekierowuje (`;`, `&&`, `|`, `>`, `$(`…), nigdy nie pasuje. Tylko
  właściciel je tworzy lub odwołuje; most nie może.
- **Nazwane referencje.** Foldery, które właściciel wymienia w `<CHIMERA_HOME>/references.json`, są
  osiągalne jako `@alias/path` tylko dla narzędzi odczytu (`read_file`, `list_dir`, `grep`, `glob`);
  każde narzędzie zapisu odmawia aliasu. Korzeń systemu plików, katalog domowy użytkownika ani folder
  danych nie mogą być referencją. Dostają je tylko powierzchnie z obecnym człowiekiem — `chat`,
  `assist`, `tui`, `solve` z obecnym człowiekiem, tura Code w desktopie (nigdy gościa) — więc boty,
  cron i `scenarios` nie mają żadnej.
- **Agenci w Markdown.** `.chimera/agents/*.md` w repozytorium to cudzy tekst, więc może tylko
  zawężać: jego narzędzia są przecinane z nadzorowanymi narzędziami sesji, jego kroki ograniczone
  krokami sesji, nieznane klucze odrzucane, jego `model` ignorowany, a jego treść ogrodzona jako
  niezaufane dane. Tylko pliki właściciela w `<CHIMERA_HOME>/agents` mogą przypiąć model z katalogu.
- **Czaty powiązane z rozmowami.** `/link` w Discordzie lub Telegramie sprawia, że czat kontynuuje
  rozmowę Code, a powiązać go może tylko właściciel wpisany na allowlistę tego bota — bez allowlisty
  nikt. Tekst obcej osoby w powiązanym pokoju nigdy nie trafia do rozmowy, a media są odrzucane, a nie
  przekazywane. Tura działa na podłodze bota, nie desktopu: tylko serwery MCP z
  `CHIMERA_BOT_MCP_SERVERS`, żadnych konektorów OpenAPI, ani zgody na powłokę, ani host exec.
- **Konektory właściciela trzymają się z dala od drzwi, które może otworzyć obcy.** Konektor OpenAPI
  wywołuje świat zewnętrzny kluczem właściciela, więc odpowiada temu, kto steruje przebiegiem. Nie
  ładuje się na powierzchni komunikatora, dopóki któryś skonfigurowany bot nie ma allowlisty, nigdy w
  zadaniu z issue GitHuba (issue może napisać każdy; sprawdzane jest tylko repozytorium), a na
  `/v1/chat/completions` tylko wtedy, gdy ustawiony jest `CHIMERA_SERVER_TOKEN`. Jeden moduł,
  `chimera/governance/surfaces.py`, decyduje, jakim rodzajem powierzchni jest nazwa, po jej rodzinie
  (`platform:discord` to bot); test zawodzi, jeśli jakiś moduł znów napisze własną listę.

## Uczciwe granice

To mierzy, czy szkodliwe działanie *już zainfekowanego* agenta zostaje zatrzymane — nie czy
model można w ogóle zainfekować. Swobodne rozumowanie nad niezaufaną prozą i eksfiltracja przez
zasadnie potrzebne narzędzia pozostają otwartymi problemami (śledzone jako
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

Pełna, zawsze aktualna polityka żyje w
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), łącznie z
tym, jak zgłosić lukę.
