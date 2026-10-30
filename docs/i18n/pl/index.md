---
source_sha256: 8f762ce8b9db1926e29af28012918efba63161223554731884d91db8361fc0a4
---

# Chimera

Agent AI podlegający nadzorowi, z rdzeniem rozumowania LLM-Fusion (panel → judge → syntezator),
routerem świadomym kosztów, jądrem governance i sandboxem.

Ta strona jest zorientowana zadaniowo: wybierz, co chcesz zrobić.

<div class="grid cards" markdown>

- **:material-rocket-launch: Zacznij**
  Zainstaluj, dodaj klucz, uruchom pierwsze zadanie w pięć minut.
  [Instalacja i pierwsze uruchomienie →](usage.md)

- **:material-toolbox: Zrób coś realnego**
  Uruchamialne przepisy: triage e-maili, codzienny brief badawczy, watchdog repozytorium.
  [Przepisy →](recipes.md)

- **:material-magnify: Inżynieria wsteczna**
  Połącz się z serwerem MCP rea, aby analizować pliki binarne, procesy i aplikacje.
  [Serwer MCP rea →](../../rea.md)

- **:material-power-plug: Podłącz narzędzia**
  Podłącz dowolny serwer MCP (GitHub, system plików, …).
  [Serwery MCP →](mcp.md)

- **:material-account-switch: Steruj innym agentem**
  Przekaż turę do Claude Code lub Gemini CLI przez ACP — i przeczytaj, co to robi z gwarancjami.
  [Agenci zewnętrzni →](external-agents.md)

- **:material-server: Obsługuj to**
  Działaj 24/7 na małym serwerze; planuj zadania; dostarczaj do czatu.
  [Wdrożenie →](deploy.md)

- **:material-shield-lock: Bezpieczeństwo**
  Governance, sandbox, śledzenie skażenia — i ich uczciwe granice.
  [Bezpieczeństwo →](security.md) · [Audyt uśpionych kanałów →](../../audits/sleeper-channels.md)

- **:material-sitemap: Zrozum to**
  Jak rdzeń fuzji, ewolucja i warstwy bezpieczeństwa łączą się w całość.
  [Architektura →](architecture.md)

- **:material-console: Każda komenda**
  Wszystkie 79, wygenerowane z samego CLI — w tym 33, których nie było w żadnej dokumentacji.
  [Referencja komend →](../../commands.md)

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
