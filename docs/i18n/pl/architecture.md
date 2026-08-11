---
source_sha256: 1f1c80dd0c5b6b4b6bade6bfbe0cf3d94969fa6ec1e9918d2e186fad3f1a2cd4
---

# Chimera — Architektura

Ten dokument mapuje bazę kodu na projekt oraz badania, na których się on opiera. Po "dlaczego"
zajrzyj do [VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md).

## Rdzeń rozumowania: LLM-Fusion

`chimera/fusion/`

Silnik fuzji przepuszcza zadanie przez **panel** modeli, każe **sędziemu** (judge) wygenerować
strukturalną analizę (konsensus / sprzeczności / częściowe pokrycie / unikalne spostrzeżenia /
martwe pola), a następnie **syntetyzator** pisze finalną odpowiedź zakotwiczoną w tej analizie
(`FusionEngine`). Implementuje protokół `SupportsComplete`, więc jest gotowym do podłączenia
backendem rozumowania wszędzie tam, gdzie oczekiwany jest model — także wewnątrz pętli agenta.

**Router świadomy kosztów** (`RoutedBackend` + `RoutingPolicy`) utrzymuje selektywność fuzji: tury
z wywołaniami narzędzi trafiają do pojedynczego modelu (fuzja nie wywołuje narzędzi), a fuzji
poddawane są tylko głębokie / wysokostawkowe tury rozumowania. Inspirowane OpenRouter Fusion (zysk
pochodzi z kroku *syntezy*, nie tylko z różnorodności modeli) oraz AURORA-AI (adaptacyjny budżet
w obrębie heterogenicznych modeli).

## Pętla agenta i autonomia Tier-2

`chimera/core/`

- `Agent` — minimalna pętla ReAct / wywołań narzędzi z **jawnym transkryptem** (stan żyje poza
  modelem). Zależy tylko od `SupportsComplete` + `ToolRegistry`.
- `AutonomousAgent` — Tier 2: złożenie kontekstu **Spine** zakresowanego przez własność →
  **plan** → snapshot → wykonanie → **przegląd Managera** (generate-vs-verify) →
  **verify-or-revert** → ponowna próba z informacją zwrotną, przy czym każda próba jest
  zapisywana w buforze doświadczeń.
- `WorkspaceGuard` — snapshot/przywracanie plików tekstowych, mechanizm stojący za
  verify-or-revert.
- `CommandVerifier` — "wykonywalny dowód" (exit 0 == sukces).

### Zwalczanie degradacji przy ciągłej ewolucji

Otwarty problem (wg *Agentic Software*, `2606.05608`): wydajność spada z >80% na izolowanych
zadaniach do ~38% przy ciągłej ewolucji — kontekst długiego horyzontu i propagacja błędów.
Przeciwdziałania Chimery, każde zakotwiczone w literaturze:

| Przeciwdziałanie | Gdzie | Podstawa |
|---|---|---|
| Eksternalizacja stanu (transkrypt/workspace, nie kontekst LLM) | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| Kontekst zakresowany przez własność (Spine) | `core/spine.py` | Spec Growth Engine `2606.27045` |
| Nadzór generate-vs-verify | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| Verify-or-revert | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| Bufor doświadczeń (porażki jako przykłady negatywne) | `evolution/experience.py` | HORIZON `2606.28279` |
| Konsolidacja wiadomości w zespołach | `orchestration/comms.py` | MOC `2606.02359` |
| Benchmark ciągłej ewolucji | `eval/continuous.py` | postawienie problemu EvoClaw |

## Pamięć i samo-ewolucja

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** — hierarchiczne elementy (working / episodic / semantic / persona) z
  `ADD / UPDATE / DELETE / NOOP` (`remember`) i deduplikacją `merge` (Memory-R1, `2606.14502`).
- **Skill evolver** — `SkillEvolver` proponuje możliwy do ponownego użycia `LearnedSkill` na
  podstawie sukcesu, testuje go i zachowuje tylko wtedy, gdy przejdzie test (propose → test →
  keep/discard). Nauczone skille to **szablony promptów, nie wykonywalny kod** — bezpieczne do
  autonomicznego tworzenia jeszcze przed samo-modyfikacją na poziomie kodu. Doprecyzowanie
  poprawia szablon na podstawie jego porażek (VIBEMed `2606.15504`).
- **Samodzielnie nauczone crony** — `CronLearner` wykrywa powtarzające się zadania i proponuje
  crony (`created_by=agent`, **wyłączone** do czasu zatwierdzenia przez człowieka).
- **Benchmark ciągłej ewolucji** — przepuszcza łańcuch zadań przez solver i raportuje degradację
  (ogólny wskaźnik sukcesu, pierwsza vs. druga połowa, najdłuższa seria).

## Governance i bezpieczeństwo

`chimera/governance/`

Samodoskonalące się jądro zaufania (AgentTrust v2, `2606.08539`):

- `TrustKernel.evaluate(action)` → **allow / warn / block / review**. Leksykalny `RuleSet`
  obsługuje deterministycznie zagrożenia o stałej sygnaturze; opcjonalny **sędzia semantyczny**
  obsługuje intencję; destylowane reguły czynią to z czasem tańszym. Niezmiennik: **nigdy nie
  blokować twardo działania nieszkodliwego**.
- `SkillValidator` / `ScheduleValidator` — **ograniczona, statycznie sprawdzalna powierzchnia
  edycji** dla samo-modyfikacji (AutoMegaKernel `2606.09682`): niebezpieczne propozycje są
  odrzucane, zanim kiedykolwiek się wykonają.
- `AuditLog` — dopisywalny (append-only) JSONL decyzji i zmian ewolucyjnych.
- `GovernedTool` / `govern_registry` — opakowuje dowolne narzędzie tak, by jego wykonanie
  podlegało bramkowaniu; komponuje się z istniejącą pętlą agenta bez zmian
  (`chimera ... --guard`).

### Warstwa skażenia (taint) — powstrzymywanie prompt injection

Nałożona na jądro — heurystyczna, uczciwa i nigdy niebędąca twardą granicą (tą jest sandbox):

- `TaintLedger` + `LedgeredTool` (`ledger.py`, `ledger_tool.py`) — rejestr uprawnień (capability
  ledger) na jeden przebieg. Pobranie (fetch) skaża swoją treść; zapis/wykonanie konsumujące
  skażoną treść **eskaluje do przeglądu** (`assess_action`). Niezaufana pobrana treść jest
  zwracana **w ogrodzeniu danych** (data-fenced) i z usuniętymi tokenami sterującymi szablonu
  czatu (`sanitize.py`), a trwałe artefakty ze skażonego przebiegu zachowują pochodzenie
  `tainted`, tak by trucizna nie mogła wyprać się do "czystej" pamięci/skilla.
- `AggregateMonitor` (`aggregate_monitor.py`) — monitor o poziom wyżej: na podstawie zdarzeń
  uprawnień każdego pod-agenta wychwytuje **rozdzielone przepływy**, których monitor na poziomie
  pojedynczego agenta nie widzi (agent A pobiera niezaufaną treść, agent B ją wykonuje lub
  **eksfiltruje**).
- `check_drift` (`drift.py`) — `Spec` wykonywalnych wymagań (`defines`/`contains`/`absent`/
  `command`), która pełni podwójną rolę: jest ziemią odniesienia (ground truth) dla
  `solve --verify` i autorytetem orkiestratora projektu w sprawie tego, co znaczy "gotowe"
  (patrz niżej). Negatywne sprawdzenia zawodzą na bezpieczną stronę (fail closed) na plikach,
  których nie potrafią przeskanować.
- `QuarantineTool` + adaptacyjna allowlista (`quarantine.py`, `allowlist.py`) — kwarantannowy
  czytnik dual-LLM/CaMeL oraz taint-adaptacyjna allowlista narzędzi, która zawęża się, gdy
  przebieg zostanie skażony.

## Zespoły wieloagentowe (Tier 3)

`chimera/orchestration/`

- `Role` + `RoleAgent` — specjalizacja ról (w stylu CrewAI).
- `SequentialCrew` — role w kolejności, każda widzi **skonsolidowane** wcześniejsze wyniki i może
  pisać do pamięci wspólnej.
- `SupervisorCrew` — workerzy zajmują się zadaniem równolegle, wyniki są konsolidowane, a
  supervisor syntetyzuje (w stylu CAPRA, `parallel_review`, `2606.18976`).
- `consolidate` — scalanie wiadomości MOC utrzymuje kontekst zespołu szczupły (`2606.02359`).

## Samo-ewoluujący ekosystem (Tier 4)

`chimera/ecosystem/`

- `MetaAgent` — projektuje/buduje/ocenia wyspecjalizowane agenty (agenty budujące agenty). Dwa
  zabezpieczenia z Meta-Agent Challenge (`2606.04455`): **izolacja narzędzi** (narzędzia
  zaprojektowanego agenta są filtrowane do dozwolonej listy) oraz **separacja testów jawnych
  i ukrytych** (przejście testu jawnego + porażka ukrytego ⇒ podejrzenie reward-hackingu, nie
  zaliczane jako sukces).
- `ChangeQueue` — reguluje *tempo* zmian (kolejka FIFO scalania + limity wsadowe), nie liczbę
  głów ("Govern the Repository", `2606.28235`).
- `TrajectoryCollector` — zapisuje (prompt, odpowiedź, wynik) i eksportuje zbiory danych
  **SFT / DPO**. Faktyczny fine-tuning jest **opcjonalny i zewnętrzny** — Chimera zbiera, nie
  trenuje.

## Ekonomia kosztów i hierarchia delegowania

`chimera/orchestration/` (hierarchy, cascade, budget, receipts, envelope_verify)

Delegowanie opłaca się tylko wtedy, gdy jest tańsze niż wykonanie pracy inline, a to twierdzenie
jest **mierzone, nie tylko deklarowane**:

- `HierarchicalOrchestrator` — dekompozycja → wysłanie budżetowanych workerów → weryfikacja
  każdego wyniku → synteza. Fan-out o charakterze odczytowym jest delegowany; trywialnie mały
  podproblem jest odpowiadany inline przez zaufany model najwyższego poziomu.
- `CascadeBackend` — słaby → bramka → średni → bramka → fuzja, eskalując tylko wtedy, gdy
  odpowiedź danego poziomu nie przechodzi taniej bramki akceptacji. **Dziennik trasy** (route
  log) rejestruje każdy przeskok, więc koszt to **suma po wszystkich wypróbowanych
  przeskokach**, nie tylko akceptowanym — eskalacje są opłacane.
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` — twardy sufit tokenów egzekwowany na
  poziomie backendu, na jednego workera.
- `EnvelopeVerifier` — schemat → kryteria akceptacji → probabilistyczna **kontrola wyrywkowa**
  (ocenia wierność podsumowania wobec surowego artefaktu); ponowne zapytanie wywołane
  niepowodzeniem kontroli wyrywkowej jest ponownie audytowane.
- **Rachunki delegowania** (`receipts.py`) — każde delegowanie loguje swoje zmierzone
  tokeny/koszt **oraz kontrfaktyk inline w tym samym wierszu**, wyceniony według stawki
  właściwej dla danego modelu (nieznany model → `None`, nigdy nie zmyślony). Narzut samego
  orkiestratora na dekompozycję/syntezę też jest mierzony, więc `summarize_delegations`
  (`chimera delegations`) raportuje **audytowalną** oszczędność netto, a `cascade-bench`
  raportuje **ogon** kosztu (p50/p95/p99), nie tylko średnią.

## Koło zamachowe samo-ewolucji

`chimera/evolution/`

"Trening", który nigdy nie dotyka wag — sterowany fitnessem, bezgradientowy i odwracalny:

- `EvolutionContext` — wspólny zestaw (experience, trajectories, memory, auto-evolver, skill
  cards, playbook), który czyni uczenie się własnością całego *stacku* agenta, nie tylko
  komendy `solve`.
- Karty skilli + doprecyzowanie **GEPA**, **playbook** ACE oraz `SkillLifecyclePolicy`, która
  promuje/degraduje skill na podstawie jego **zmierzonych** statystyk użycia/sukcesu (nowy
  skill rodzi się jako `provisional`).
- **Diff-gate** — "pusty sukces" (weryfikator przeszedł, ale diff workspace jest pusty) nie
  tworzy skilla ani wpisu pamięci; koło zamachowe uczy się tylko z pracy, która faktycznie się
  wydarzyła.
- **Transfer-gate** (`eval/transfer.py`) — dostrojony artefakt jest promowany tylko wtedy, gdy
  utrzymuje się też na zbiorze holdout, co chroni przed negatywnym transferem.
  `maturity.Scorecard.weakest()` jest celem: pętla mierzy w najsłabszą zdolność. Regresje są
  automatycznie wycofywane tylko przy **statystycznie istotnym** spadku (przedział ufności,
  nigdy pojedynczy punkt).

Każda zmiana ustawienia domyślnego jest odblokowywana za **wcześniej zarejestrowanym**,
sparowanym testem A/B (`bench/`), publikowanym niezależnie od tego, czy wygrywa, czy przegrywa —
bez ponownego rzucania kośćmi w poszukiwaniu istotności.

## Autonomia projektu (od początku do końca)

`chimera/orchestration/project.py`

`ProjectOrchestrator` prowadzi cały projekt wobec `Spec`: graf zadań (DAG w stylu Kanban z
`depends_on`) → każda gotowa karta jest rozwiązywana (z powyższym kontekstem ewolucji) →
**odebrana wobec Spec** przez `check_drift` (jedyny autorytet w sprawie "gotowe") → niespełnione
wymagania generują kolejne karty, w pętli, aż Spec zostanie spełniona lub zatrzyma ją budżet /
maksymalna liczba iteracji / punkt kontrolny człowieka. Ryzykowne kroki (`risk: high` — deploy /
migracja / usunięcie) **wstrzymują się do zatwierdzenia przez człowieka**; przebieg jest trwały
i wznawialny.

## Sprawy przekrojowe

- **Providerzy** (`providers/`) — jedna, niezależna od providera brama nad LiteLLM; klucze mogą
  żyć w `.env` i są eksportowane do środowiska, tak by LiteLLM je widział.
- **Narzędzia** (`tools/`) — natywne prymitywy; metadane narzędzi są atrybutami instancji, dzięki
  czemu dynamicznie generowane narzędzia (OpenAPI/MCP) działają.
- **Integracje** (`integrations/`) — klient MCP (opcjonalny extra `mcp`) + importer
  OpenAPI→narzędzie + rejestr konektorów.
- **Scheduler** (`scheduler/`) — crony + SOP zdarzeniowe; czas jest wstrzykiwany dla
  deterministycznych testów.
- **Migracja** (`migration/`) — importuje konfigurację + skille + **scala** pamięć długotrwałą
  z Hermes / OpenClaw, z deduplikacją i bez destrukcji.

## Filozofia testowania

Każdy podsystem jest testowany jednostkowo z **fałszywymi backendami** — deterministycznie, bez
sieci, bez kluczy. Komendy, które faktycznie wywołują LLM, są testowane dymnie (smoke-tested) pod
kątem ścieżki błędu przy braku klucza. Bramka jakości (`ruff` + `mypy --strict` + `pytest`)
działa w CI na Pythonie 3.11 i 3.12.
