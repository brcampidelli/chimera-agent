---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
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
  ścieżkę webhooka.
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

## 5. Uczciwy status

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
