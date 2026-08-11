---
source_sha256: 1f1c80dd0c5b6b4b6bade6bfbe0cf3d94969fa6ec1e9918d2e186fad3f1a2cd4
---

# Chimera — Architecture

Ce document met en correspondance le code avec la conception et avec la recherche sur
laquelle il s'appuie. Pour le « pourquoi », voir
[VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md).

## Le cœur de raisonnement : LLM-Fusion

`chimera/fusion/`

Le moteur de fusion fait passer une tâche à travers un **panel** de modèles, fait produire par
un **juge** une analyse structurée (consensus / contradictions / couverture partielle /
éclairages uniques / angles morts), puis un **synthétiseur** rédige la réponse finale ancrée
dans cette analyse (`FusionEngine`). Il implémente le protocole `SupportsComplete`, ce qui en
fait un backend de raisonnement interchangeable partout où un modèle est attendu — y compris à
l'intérieur de la boucle de l'agent.

Un **routeur sensible au coût** (`RoutedBackend` + `RoutingPolicy`) garde la fusion sélective :
les tours d'appel d'outils vont vers un seul modèle (la fusion n'appelle pas d'outils), et
seuls les tours de raisonnement profond / à enjeu élevé sont fusionnés. Inspiré d'OpenRouter
Fusion (le gain vient de l'étape de *synthèse*, pas seulement de la diversité des modèles) et
d'AURORA-AI (budget adaptatif entre modèles hétérogènes).

## La boucle de l'agent & l'autonomie de Tier 2

`chimera/core/`

- `Agent` — une boucle ReAct / d'appel d'outils minimale avec une **transcription explicite**
  (l'état vit en dehors du modèle). Ne dépend que de `SupportsComplete` + un `ToolRegistry`.
- `AutonomousAgent` — Tier 2 : assembler un contexte **Spine** délimité par propriété
  (ownership-scoped) → **plan** → snapshot → exécution → **révision Manager**
  (generate-vs-verify) → **verify-or-revert** → nouvelle tentative avec retour, en enregistrant
  chaque essai dans le buffer d'expérience.
- `WorkspaceGuard` — snapshot/restauration de fichiers texte, le mécanisme derrière
  verify-or-revert.
- `CommandVerifier` — « preuve exécutable » (exit 0 == succès).

### S'attaquer à la dégradation en évolution continue

Le problème ouvert (selon *Agentic Software*, `2606.05608`) : la performance chute de >80 % sur
des tâches isolées à ~38 % en évolution continue — contexte à long horizon + propagation
d'erreurs. Les contre-mesures de Chimera, chacune ancrée dans la littérature :

| Contre-mesure | Où | Base |
|---|---|---|
| Externaliser l'état (transcription/workspace, pas le contexte du LLM) | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| Contexte délimité par propriété (Spine) | `core/spine.py` | Spec Growth Engine `2606.27045` |
| Supervision generate-vs-verify | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| Verify-or-revert | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| Buffer d'expérience (échecs comme négatifs) | `evolution/experience.py` | HORIZON `2606.28279` |
| Consolidation des messages en équipe | `orchestration/comms.py` | MOC `2606.02359` |
| Benchmark d'évolution continue | `eval/continuous.py` | énoncé du problème EvoClaw |

## Mémoire & auto-évolution

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** — items hiérarchiques (working / episodic / semantic / persona) avec
  `ADD / UPDATE / DELETE / NOOP` (`remember`) et une déduplication `merge` (Memory-R1,
  `2606.14502`).
- **Skill evolver** — `SkillEvolver` propose une `LearnedSkill` réutilisable à partir d'un
  succès, la teste, et ne la garde que si elle passe (propose → test → keep/discard). Les
  skills apprises sont des **modèles de prompt, pas du code exécutable** — sûr à générer de
  façon autonome avant toute auto-modification au niveau du code. Le raffinement améliore un
  modèle à partir de ses échecs (VIBEMed `2606.15504`).
- **Crons auto-appris** — `CronLearner` détecte les tâches récurrentes et propose des crons
  (`created_by=agent`, **désactivés** en attente d'approbation humaine).
- **Benchmark d'évolution continue** — fait passer une chaîne de tâches à travers un solveur et
  rapporte la dégradation (taux de réussite global, première moitié vs seconde moitié, plus
  longue série).

## Gouvernance & sécurité

`chimera/governance/`

Un noyau de confiance auto-améliorant (AgentTrust v2, `2606.08539`) :

- `TrustKernel.evaluate(action)` → **allow / warn / block / review**. Le `RuleSet` lexical gère
  les menaces à signature fixe de façon déterministe ; un **juge sémantique** optionnel gère
  l'intention ; des règles distillées le rendent moins cher avec le temps. Invariant : **ne
  jamais bloquer en dur une action bénigne**.
- `SkillValidator` / `ScheduleValidator` — la **surface d'édition contrainte, vérifiable
  statiquement** pour l'auto-modification (AutoMegaKernel `2606.09682`) : les propositions
  dangereuses sont rejetées avant même de s'exécuter.
- `AuditLog` — JSONL en ajout seul des décisions et des changements d'évolution.
- `GovernedTool` / `govern_registry` — encapsule n'importe quel outil pour que son exécution
  soit filtrée ; se compose avec la boucle d'agent existante sans changement
  (`chimera ... --guard`).

### La couche de contamination (containment de l'injection de prompt)

Superposée au noyau — heuristique, honnête, et jamais une frontière dure (le sandbox, lui,
l'est) :

- `TaintLedger` + `LedgeredTool` (`ledger.py`, `ledger_tool.py`) — un registre de capacités par
  run. Une récupération contamine son contenu ; une écriture/exécution qui consomme du contenu
  contaminé **escalade vers une révision** (`assess_action`). Le contenu non fiable récupéré
  est renvoyé **clôturé comme donnée (data-fenced)** et avec les tokens de contrôle du
  chat-template retirés (`sanitize.py`), et les artefacts durables d'un run contaminé
  conservent une provenance `tainted` pour que le poison ne puisse pas se blanchir dans une
  mémoire/skill « propre ».
- `AggregateMonitor` (`aggregate_monitor.py`) — un moniteur un niveau au-dessus : à partir des
  événements de capacité de chaque sous-agent, il détecte les **flux scindés** qu'un moniteur
  par agent ne peut pas voir (l'agent A récupère du contenu non fiable, l'agent B l'exécute ou
  l'**exfiltre**).
- `check_drift` (`drift.py`) — un `Spec` d'exigences exécutables (`defines`/`contains`/`absent`/
  `command`) qui sert à la fois de vérité terrain pour `solve --verify` et d'autorité pour
  l'orchestrateur de projet sur « terminé » (voir plus bas). Les vérifications négatives
  échouent par défaut (fail closed) sur les fichiers qu'elles ne peuvent pas scanner.
- `QuarantineTool` + liste blanche adaptative (`quarantine.py`, `allowlist.py`) — un lecteur en
  quarantaine dual-LLM/CaMeL et une liste blanche d'outils adaptative à la contamination qui se
  restreint une fois un run contaminé.

## Équipes multi-agents (Tier 3)

`chimera/orchestration/`

- `Role` + `RoleAgent` — spécialisation par rôle (à la CrewAI).
- `SequentialCrew` — les rôles s'enchaînent dans l'ordre, chacun voit les sorties précédentes
  **consolidées** et peut écrire dans la mémoire partagée.
- `SupervisorCrew` — les workers traitent la tâche en parallèle, les sorties sont consolidées,
  et un superviseur synthétise (à la CAPRA `parallel_review`, `2606.18976`).
- `consolidate` — la fusion de messages MOC garde le contexte d'équipe compact (`2606.02359`).

## Écosystème auto-évolutif (Tier 4)

`chimera/ecosystem/`

- `MetaAgent` — conçoit/construit/évalue des agents spécialisés (des agents qui construisent
  des agents). Deux garde-fous issus du Meta-Agent Challenge (`2606.04455`) : **isolation des
  outils** (les outils d'un agent conçu sont filtrés à une liste autorisée) et **séparation des
  tests cachés** (réussite visible + échec caché ⇒ reward-hacking suspecté, non crédité comme
  succès).
- `ChangeQueue` — gouverne le *tempo* des changements (file de fusion FIFO + plafonds par lot),
  pas les effectifs (« Govern the Repository », `2606.28235`).
- `TrajectoryCollector` — enregistre (prompt, réponse, résultat) et exporte des jeux de données
  **SFT / DPO**. Le fine-tuning réel est **opt-in et externe** — Chimera collecte, il n'entraîne
  pas.

## Économie des coûts & la hiérarchie de délégation

`chimera/orchestration/` (hierarchy, cascade, budget, receipts, envelope_verify)

La délégation ne paie que lorsqu'elle est moins chère que de faire le travail en ligne, et
l'affirmation est **mesurée, pas assertée** :

- `HierarchicalOrchestrator` — décomposer → distribuer des workers budgétisés → vérifier chaque
  résultat → synthétiser. Un fan-out en forme de lecture délègue ; une sous-tâche trivialement
  petite est répondue en ligne par le modèle de confiance du sommet.
- `CascadeBackend` — faible → porte → intermédiaire → porte → fusion, en montant seulement
  quand la réponse d'un palier échoue une porte d'acceptation bon marché. Le **journal de
  routage** enregistre chaque saut, si bien que le coût est la **somme sur les sauts tentés**,
  pas seulement celui accepté — les escalades sont payées.
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` — un plafond de tokens dur appliqué au
  niveau du backend, par worker.
- `EnvelopeVerifier` — schéma → critères d'acceptation → **vérification ponctuelle**
  probabiliste (noter la fidélité d'un résumé par rapport à l'artefact brut) ; une nouvelle
  demande déclenchée par un échec ponctuel est ré-auditée.
- **Receipts de délégation** (`receipts.py`) — chaque délégation enregistre ses tokens/coût
  mesurés **et le contrefactuel en ligne sur la même ligne**, chiffré au tarif propre de chaque
  modèle (modèle inconnu → `None`, jamais fabriqué). La surcharge propre de
  décomposition/synthèse de l'orchestrateur est aussi mesurée, si bien que
  `summarize_delegations` (`chimera delegations`) rapporte une économie nette **auditable**, et
  `cascade-bench` rapporte la **queue** de coût (p50/p95/p99), pas seulement la moyenne.

## Le volant d'inertie de l'auto-évolution

`chimera/evolution/`

Le « entraînement » qui ne touche jamais aux poids — signalé par la fitness, sans gradient, et
réversible :

- `EvolutionContext` — l'assemblage partagé (expérience, trajectoires, mémoire, auto-évolveur,
  cartes de skills, playbook) qui fait de l'apprentissage une propriété de la *pile* d'agent,
  pas seulement de la commande `solve`.
- Cartes de skills + raffinement **GEPA**, **playbook** ACE, et une `SkillLifecyclePolicy` qui
  promeut/rétrograde une skill selon ses statistiques d'usage/succès **mesurées** (une nouvelle
  skill naît `provisional`).
- La **diff-gate** — un « succès creux » (le vérificateur passe mais le diff du workspace est
  vide) ne forge ni skill ni mémoire ; le volant n'apprend que du travail qui a réellement eu
  lieu.
- La **transfer-gate** (`eval/transfer.py`) — un artefact ajusté n'est promu que s'il tient
  aussi sur un holdout, ce qui protège contre le transfert négatif. `maturity.Scorecard.weakest()`
  est l'objectif : la boucle cible la capacité la plus faible. Les régressions ne sont
  auto-annulées que sur une chute **statistiquement significative** (un IC, jamais un seul
  point).

Chaque basculement d'un défaut est verrouillé derrière un A/B apparié **pré-enregistré**
(`bench/`), publié qu'il gagne ou qu'il perde — pas de relance en boucle pour obtenir de la
significativité.

## Autonomie de projet (de bout en bout)

`chimera/orchestration/project.py`

`ProjectOrchestrator` exécute un projet entier par rapport à un `Spec` : graphe de tâches (un
DAG Kanban avec `depends_on`) → chaque carte prête résolue (avec le contexte d'évolution
ci-dessus) → **acceptée par rapport au Spec** via `check_drift` (la seule autorité sur
« terminé ») → les exigences non satisfaites génèrent les cartes suivantes, en bouclant jusqu'à
ce que le Spec soit aligné ou qu'un budget / un nombre max d'itérations / un point de contrôle
humain l'arrête. Les étapes risquées (`risk: high` — déploiement / migration / suppression)
**se mettent en pause pour une approbation humaine** ; le run est durable et reprenable.

## Transversal

- **Providers** (`providers/`) — une passerelle agnostique au fournisseur au-dessus de
  LiteLLM ; les clés peuvent vivre dans `.env` et sont exportées vers l'environnement pour que
  LiteLLM les voie.
- **Tools** (`tools/`) — primitives natives ; les métadonnées d'outil sont des attributs
  d'instance pour que les outils générés dynamiquement (OpenAPI/MCP) fonctionnent.
- **Integrations** (`integrations/`) — client MCP (extra `mcp` optionnel) + importateur
  OpenAPI→tool + registre de connecteurs.
- **Scheduler** (`scheduler/`) — crons + SOP événementiels ; le temps est injecté pour des
  tests déterministes.
- **Migration** (`migration/`) — importe la config + les skills + **fusionne** la mémoire à
  long terme depuis Hermes / OpenClaw, dédupliquée et non destructive.

## Philosophie de test

Chaque sous-système est testé unitairement avec des **backends factices** — déterministes, sans
réseau, sans clés. Les commandes qui appellent réellement un LLM sont testées en fumée (smoke
test) pour leur chemin d'échec sans clé. La barrière de qualité (`ruff` + `mypy --strict` +
`pytest`) tourne en CI sur Python 3.11 et 3.12.
