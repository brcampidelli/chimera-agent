---
source_sha256: bec93da7acb7d3a5290f324d0095c1da6c043ab5acb014a8cbcf4a63ada31510
---

# Wdrażanie Chimery na serwerze (VPS)

Chimera działa jako długo żyjący proces **gateway**. Dodaj `--cron`, a będzie też odpalać
zaplanowane zadania wg prawdziwego zegara, więc *działa w czasie* (nie tylko gdy dostanie
wiadomość). Ten przewodnik pokrywa wdrożenie na VPS za 5$ na dwa sposoby: **Docker Compose**
(zalecane) lub **systemd**.

Stan — pamięć długoterminowa, zadania cron, trajektorie, dziennik audytu — żyje w `CHIMERA_HOME`
(katalogu). Zachowaj go trwale (wolumen Dockera albo prawdziwa ścieżka), a agent przetrwa
restarty.

---

## 0. Wymagania wstępne

- Linuksowy VPS (1 vCPU / 1 GB RAM w zupełności wystarczy dla pojedynczego agenta).
- Co najmniej jeden klucz providera. Najtańszy start to klucz OpenRouter.
- Dla publicznych przychodzących webhooków (WhatsApp Cloud API, `POST /webhook/<hook>`),
  domena + reverse proxy z TLS (Caddy lub nginx). Niepotrzebne dla Discorda/Telegrama/Slacka/
  Signala, które łączą się wychodząco.

Stwórz swój plik env z szablonu i wypełnij klucz:

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose (zalecane)

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

To uruchamia `chimera serve --host 0.0.0.0 --cron`: bramę HTTP (`/chat`, `/webhook/<hook>`,
`/health`) **oraz** daemon cron. Stan jest zachowywany w wolumenie `chimera-data`.

**Podpięcie platformy czatu** (na przykładzie Discorda) — ustaw token w `.env`, następnie nadpisz
komendę w `docker-compose.yml`:

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

i ponownie `docker compose up -d`. (Telegram/Slack/Signal działają tak samo przez swoje flagi;
każdy potrzebuje swojego dopasowanego tokenu `CHIMERA_*` — zobacz `.env.example`.)

**Aktualizacja do nowej wersji:**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd (bez Dockera)

Zainstaluj w virtualenv na hoście:

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

Stwórz `/etc/systemd/system/chimera.service`:

```ini
[Unit]
Description=Chimera Agent gateway + cron daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/chimera
EnvironmentFile=/opt/chimera/.env
Environment=CHIMERA_HOME=/opt/chimera/state
ExecStart=/opt/chimera/.venv/bin/chimera serve --host 0.0.0.0 --cron
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chimera
sudo systemctl status chimera
journalctl -u chimera -f
```

---

## 3. Planowanie pracy proaktywnej (daemon `--cron`)

`--cron` tylko *uruchamia* zadania, które zaplanowałeś. Dodaj je przez CLI (zachowują się w
`CHIMERA_HOME`):

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Wewnątrz Dockera:

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

Daemon tyka co `--cron-tick` sekund (domyślnie 30) i wysyła akcję każdego należnego zadania przez
agenta, gdy nadejdzie jego czas. Nieudane zadanie jest logowane i nigdy nie zatrzymuje daemona.

### Zapytać harmonogram o to, czego nie mówi

```bash
chimera cron doctor
```

Harmonogram może zamilknąć na dwa sposoby, a dopóki nie zapytasz, oba wyglądają tak samo — jak
harmonogram, w którym nic nie jest należne:

- **Nic się nie wykonało.** Daemon umarł, kontener nigdy nie został zrestartowany, host zasnął.
  Żadnego wyjątku, żadnej linii logu, żadnego werdyktu. Każdy inny mechanizm uczciwości tutaj stoi
  *poniżej faktu, że przebieg się odbył*, więc żaden z nich nie dostaje swojej kolejki.
- **Wszystko się wykonało i wszystko przepadło.** Daemon żyje, `last_run` był minutę temu,
  harmonogram idzie do przodu — a każde wysłanie zawodzi od miesiąca. Ten przypadek czyta się jako
  *zdrowszy* od pierwszego, bo pole, które wygląda na oznakę zdrowia, zapisuje próbę, a nie wynik.

`cron doctor` zadaje oba pytania i daje różne rady, bo naprawy nie mają ze sobą nic wspólnego:
spóźnienie dotyczy daemona, zawodzenie dotyczy zadania. `chimera cron list` wypisuje linię, gdy
cokolwiek zawodzi, więc nie musisz wiedzieć, że ta komenda istnieje, żeby się o tym dowiedzieć.

**Czym to nie jest.** To pytanie, a nie stróż: dopóki proces leży, nikt tutaj niczego nie zauważa, z
tego samego powodu, dla którego proces, który się wywalił, nie może zalogować własnego upadku.
Odpowiada uczciwie w chwili, gdy cokolwiek zapyta — powłoka, aplikacja, następny start. Prawdziwy
stróż potrzebuje własnego zegara i własnej żywotności, co jest osobną decyzją i jest
[śledzone jako issue #26](https://github.com/brcampidelli/chimera-agent/issues/26). Jeśli chcesz
alertu, a nie odpowiedzi, uruchamiaj to z crona samego hosta:

```cron
*/30 * * * * cd /opt/chimera && .venv/bin/chimera cron doctor | mail -s "chimera" you@example.com
```

To działa, bo nadzoruje to coś innego niż Chimera — i o to właśnie chodzi.

---

## 4. Zdrowie, kopie zapasowe, bezpieczeństwo

- **Zdrowie:** `GET /health` zwraca `{"ok": true}`. Compose ma podpięty healthcheck.
- **Kopie zapasowe:** kopiuj wolumen `chimera-data` (Docker) lub katalog `CHIMERA_HOME`
  (systemd) — to cały trwały stan. Przykład: `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **Sekrety:** trzymaj klucze w `.env` (ignorowanym przez git); nigdy nie wypiekaj ich do obrazu.
- **Ekspozycja:** wiąż bramę z `0.0.0.0` tylko za firewallem/reverse proxy. Ustaw
  **`CHIMERA_SERVER_TOKEN`**, by wymagać `Authorization: Bearer <token>` na bramie HTTP i API
  desktopowym (token jest automatycznie przekazywany UI desktopowemu tylko dla klientów loopback,
  więc instancja wystawiona zdalnie pozostaje za twoją własną autoryzacją). Autoryzacja jest
  opt-in i domyślnie pusta, więc bez tej zmiennej jej nie ma — ogranicz port albo wystaw tylko
  ścieżkę webhooka. Jak dotrzeć do tej instancji z aplikacji desktopowej, zobacz
  [§5](#5-reaching-this-instance-from-the-desktop-app).
- **Sandboxing:** ustaw `CHIMERA_SANDBOX=docker`, by uruchamiać narzędzia shell/kod w
  jednorazowym kontenerze zamiast na hoście.
- **Wykonanie bez nadzoru na hoście:** od 2026-07-20 przebieg headless **odmawia** komend
  hostowych pod domyślnym `CHIMERA_HOST_EXEC=ask` (nie ma TTY do potwierdzenia). Wdrożenie, które
  naprawdę potrzebuje, by agent uruchamiał shell na hoście, ustawia `CHIMERA_HOST_EXEC=allow`
  świadomie; bezpieczniejszą opcją jest `CHIMERA_SANDBOX=docker`, gdzie ta bramka jest pomijana,
  bo kontener naprawdę izoluje. Podobnie serwer API uzbraja zawężanie skażenia
  (`CHIMERA_TAINT_NARROW=1`): po tym, jak agent przeczyta niezaufaną treść, narzędzia
  wykonania/zapisu/wychodzące zawodzą na bezpieczną stronę (fail closed). Ustaw na `0`, by dalej
  działać autonomicznie.

---

## 5. Dotarcie do tej instancji z aplikacji desktopowej

Aplikacja desktopowa domyślnie rozmawia z tą Chimerą, którą sama uruchamia na twojej maszynie. Od
v0.44 może też wskazywać na taką, którą prowadzisz sam — ten VPS — i wtedy aplikacja staje się oknem
na agenta, który i tak całą noc wykonuje twoje zadania cron.

**Przeczytaj tę część, zanim otworzysz port.** To, co wystawiasz, nie jest panelem podglądu. Każdy
ekran tej aplikacji jest powierzchnią sterowania: uruchamia powłokę, edytuje pliki, rozdziela
tablicę autonomicznych zadań i zmienia ustawienia. Instancja osiągalna z internetu bez tokenu to nie
„Chimera, na którą ktoś mógłby popatrzeć" — to maszyna, na której każdy, kto znajdzie adres, może
uruchamiać polecenia, opłacane twoimi kluczami providera.

Muszą być spełnione trzy rzeczy, a bez dwóch pierwszych aplikacja odmawia połączenia:

**1 — TLS.** Postaw ją za reverse proxy z prawdziwym certyfikatem (Caddy załatwi go za ciebie):

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

Aplikacja odmawia adresu bez `https` poza twoją własną maszyną, bo token jedzie w nagłówku
`Authorization` przy **każdym** żądaniu — po zwykłym http to poświadczenie wręczone każdemu
przeskokowi między tobą a serwerem, a nic na ekranie nie wyglądałoby przy tym źle.

**2 — Token.** Autoryzacja jest opt-in i domyślnie pusta:

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

Wpisz go do `.env`, zrestartuj i wklej tę samą wartość do aplikacji. Aplikacja odmawia zdalnego
adresu bez tokenu z powodu powyżej: instancja bez niego stoi otworem dla każdego, kto ją znajdzie.

Zwróć uwagę, czego serwer celowo **nie** robi: gdy zdalny klient prosi o UI, serwuje stronę *bez*
tokenu. Token nigdy nie jest wydawany przez sieć — kopiujesz go do własnego klienta, raz, poza
pasmem. Dlatego aplikacja ma na niego pole.

**3 — Origin twojej aplikacji.** Aplikację serwuje jej własny lokalny sidecar, więc jej żądania do
tej instancji są cross-origin, a przeglądarka odrzuca odpowiedzi, dopóki ta instancja nie nazwie
tego originu:

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

Aplikacja pokazuje dokładną wartość, gdy połączenie się nie uda — jest w komunikacie błędu, gotowa
do skopiowania. Port jest stały dla instalacji (od v0.43 jest pamiętany między uruchomieniami), więc
ustawia się to raz na każdą maszynę, z której się łączysz. Kilka oddziela się przecinkami.

**To ustawienie nie jest granicą bezpieczeństwa i nie wolno go tak czytać.** CORS decyduje, która
*strona* może odczytać odpowiedź; o tym, kto może *wołać*, nie decyduje nic. Bramką jest token.
Nazwanie originu bez ustawienia tokenu niczego nie chroni — sprawia tylko, że niezabezpieczona
instancja jest osiągalna z przeglądarki, a nie tylko z `curl`.

Domyślnie puste, więc instancja, której nikt nie skonfigurował, zachowuje się dokładnie tak jak
wcześniej.

### Co aplikacja mówi, gdy się nie uda

- **„Token został odrzucony"** — adres i origin są dobre; wartość jest zła.
- **„Nie udało się dosięgnąć"** — albo adres jest zły, albo origin nie jest dozwolony.
  Przeglądarka celowo odmawia powiedzenia, który z nich, więc aplikacja wymienia oba, zamiast
  zgadywać, i podaje ci origin do dopuszczenia.
- **Ostrzeżenie o wersji** — aplikacja porównuje wersję własnego backendu z tą i podaje obie liczby.
  Nie odmawia: serwer o jedno wydanie z tyłu zwykle działa, a odmowa zostawiłaby cię na tym samym
  ekranie, który byłby potrzebny do naprawy. Niektóre endpointy mogą po starszej stronie nie
  istnieć.

### Jeszcze bezpieczniej

Pomiń publiczny port całkowicie: dosięgnij VPS-a przez WireGuard albo tailnet Tailscale i skieruj
aplikację na adres prywatny. Token nadal ma znaczenie — tailnet to mniejszy pokój, a nie pusty.

---

## 6. Uczciwy status

Chimera jest w fazie **alpha**. To się wdraża i działa, a daemon cron czyni ją proaktywną — ale
nie ma jeszcze **żadnego przebiegu produkcyjnego**. Zacznij od cronów niskiego ryzyka, obserwuj
`logs`, i miej na uwadze zabezpieczenia governance (`--guard` przy `solve`,
`CHIMERA_SANDBOX=docker`) przy wszystkim, co dotyka prawdziwych systemów.

## Gdzie te strony są publikowane

Te pliki są źródłem dla dokumentacji na **chimeraagent.space**, która renderuje je bezpośrednio z
tego katalogu w czasie builda. Edytuj markdown tutaj, a strona podąży za tym; nie ma drugiej
kopii do utrzymywania w zgodzie.

Konfiguracja MkDocs, która kiedyś żyła w `mkdocs.yml`, została usunięta. Była kompletna —
motyw, nawigacja, dziesięć stron — i nigdy nie została opublikowana: nie było workflow ani gałęzi
`gh-pages`, więc instrukcje wdrożenia, które kiedyś stały w tym miejscu, opisywały stronę, która
nie istniała. Konfiguracja, której nikt nie uruchamia, jest gorsza niż brak konfiguracji, bo
następna osoba edytuje jej nawigację i nie potrafi ustalić, dlaczego nic się nie zmienia.
