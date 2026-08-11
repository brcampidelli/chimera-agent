---
source_sha256: fe1348e242b1851c75bb1525ecf723afe068c499ed372335aca5e281cc72ba04
---

# Chimera

Otwartoźródłowy (Apache-2.0), samo-ewoluujący agent AI, którego rdzeń rozumowania **fuzjonuje
kilka modeli** (panel → judge → syntetyzator) za routerem świadomym kosztów — z jądrem
governance, sandboxem i pamięcią, która się uczy.

Ta strona jest zorientowana zadaniowo: wybierz, co chcesz zrobić.

<div class="grid cards" markdown>

- **:material-rocket-launch: Zacznij**
  Zainstaluj, dodaj klucz, uruchom pierwsze zadanie w pięć minut.
  [Instalacja i pierwsze uruchomienie →](usage.md)

- **:material-toolbox: Zrób coś realnego**
  Uruchamialne przepisy: triage e-maili, codzienny brief badawczy, watchdog repozytorium.
  [Przepisy →](recipes.md)

- **:material-power-plug: Podłącz narzędzia**
  Podłącz dowolny serwer MCP (GitHub, system plików, …).
  [Serwery MCP →](mcp.md)

- **:material-server: Obsługuj to**
  Działaj 24/7 na małym serwerze; planuj zadania; dostarczaj do czatu.
  [Wdrożenie →](deploy.md)

- **:material-shield-lock: Bezpieczeństwo**
  Governance, sandbox, śledzenie skażenia — i ich uczciwe granice.
  [Bezpieczeństwo →](security.md)

- **:material-sitemap: Zrozum to**
  Jak rdzeń fuzji, ewolucja i warstwy bezpieczeństwa łączą się w całość.
  [Architektura →](architecture.md)

</div>

## Jedna linia

```bash
uv sync --extra dev && uv run chimera init
```

Następnie wypróbuj `chimera run "..."`, albo prawdziwy przepis:

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## Uczciwe domyślnie

Chimera jest w fazie **alpha**. Dostarcza obronę wielowarstwową (defense-in-depth), ale
dokumentacja wprost mówi, gdzie kończy się każde zabezpieczenie — obrony przed injection nawet
publikują zmierzoną liczbę (`chimera redteam`). Zobacz [Bezpieczeństwo](security.md).
