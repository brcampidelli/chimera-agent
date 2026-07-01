<div align="center">

<img src="assets/logo-wide.png" alt="Logo Chimera" width="460" />

# Chimera

**Un agent IA open-source et auto-évolutif dont le cœur de raisonnement est un moteur de Fusion de LLMs.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-rejoindre-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <b>Français</b> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

Chimera fusionne **plusieurs LLMs par requête** — un pipeline **panel → juge → synthétiseur**
inspiré d'OpenRouter Fusion — au lieu de s'appuyer sur un unique modèle de pointe, et
**s'améliore au fil du temps** (mémoire → skills → modèle), tout en résistant à la
*dégradation par évolution continue* qui limite les agents actuels.

> **Statut :** développement précoce (0.1.x). Le plan de construction complet (M0–M7) est
> implémenté — Tiers 1–4 + le moteur de Fusion + auto-évolution multi-niveaux + un noyau de
> gouvernance — plus une **boucle d'apprentissage comportemental fermée**, une **couche
> opérationnelle** (Kanban + worker lanes, crew SDLC, un DSL déclaratif de boucles), un
> **isolement d'exécution** (sandbox Docker + git worktrees) et les **techniques des papers**
> autour desquelles il a été conçu (HORIZON, VIBEMed, Spec Growth, AgentTrust v2,
> AutoMegaKernel, Meta-Agent, MOC).
> 332 tests (+ intégration en direct opt-in) · `mypy --strict` propre · `ruff` propre.

---

## Pourquoi Chimera

Les frameworks existants sont forts sur un seul axe : Hermes/OpenClaw font évoluer les skills mais
utilisent un seul modèle ; CrewAI/LangGraph orchestrent bien mais n'apprennent pas ;
TrustClaw/NemoClaw/ZeroClaw apportent sécurité/sandbox mais n'évoluent pas. **Chimera combine les quatre :**

- 🧬 **La Fusion comme raisonnement** — le moteur panel→juge→synthétiseur est le cœur de raisonnement, pas un module additionnel. Le gain vient de l'étape de *synthèse* elle-même, pas seulement de la diversité des modèles.
- 🪜 **Quatre niveaux de capacité dans une progression** — outils augmentés → autonome mono-tâche → équipes multi-agents → écosystème auto-évolutif.
- ♻️ **Une boucle d'auto-évolution multi-niveaux fermée** qui attaque explicitement la dégradation par évolution continue (état externalisé, contexte résistant au drift, verify-or-revert, un buffer d'expérience réinjecté dans la planification).
- 🛡️ **Un noyau de gouvernance qui s'améliore aussi** — allow/warn/block/review, avec une surface d'auto-modification validée statiquement et un précédent gardé.

## Fonctionnalités

**Raisonnement & autonomie**
- **Moteur LLM-Fusion** — panel agnostique de fournisseur de modèles de pointe + ouverts, un juge qui fait ressortir consensus/contradictions/angles morts, et un synthétiseur ; un **routeur conscient du coût** ne fusionne que lorsque c'est rentable (les tours d'outils restent en modèle unique).
- **Autonomie Tier-2** — planifier → exécuter → revue du Manager → **verify-or-revert** (snapshot/restauration du workspace + un vérificateur par commande), avec **isolement en git worktree** (`solve --isolate`) — les modifications n'arrivent que si elles sont vérifiées.
- **Crew de cycle de vie SDLC** (`chimera lifecycle`) — un pipeline pré-assemblé **plan → build → test → review** avec verify-or-revert à l'étape de test.
- **Équipes multi-agents** — spécialisation par rôles, crews séquentielles et superviseur, consolidation MOC, mémoire partagée, revue parallèle.

**Auto-évolution & gouvernance**
- **Boucle comportementale fermée** — les échecs passés alimentent le planner (leçons), les succès vérifiés écrivent automatiquement la mémoire, et les tâches récurrentes auto-évoluent une skill validée et smoke-testée — le tout gardé par verify-or-revert. Plus un benchmark d'évolution continue et un stress test EvoClaw naive-vs-guarded.
- **Mémoire hiérarchique** — working / episodic / semantic / persona **+ une couche graph** (`memory graph`) qui rappelle les faits par entité, pas seulement par mot-clé.
- **Évolution de modèle opt-in** — `solve` collecte des trajectoires ; `evolve` transforme en datasets SFT/DPO et émet une recette LoRA exécutable. L'entraînement reste externe/opt-in.
- **Noyau de gouvernance** — allow/warn/block/review (règles lexicales + juge sémantique optionnel, avec distillation de règles et un **dépôt de précédents gardé**), un validateur statique pour la surface d'auto-modification, un journal d'audit append-only, des outils gouvernés, un **modèle de changement à 4 acteurs** et un **gate de drift spec↔code** (`chimera drift`).

**Fournisseurs**
- **N'importe quel modèle, une interface** — agnostique de fournisseur via LiteLLM (100+ modèles via des slugs `provider/model`) ; clés first-class pour OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Auto-hébergé & résilient** — endpoints personnalisés pour **Ollama/vLLM** (`CHIMERA_API_BASE`), **chaînes de fallback**, **credential pools** avec rotation round-robin, un changement de modèle **`/model`** en direct et un **prompt caching** (`CHIMERA_CACHE`) pour les tours de raisonnement répétés.

**Orchestration, interfaces & intégrations**
- **Kanban + worker lanes** (`chimera kanban`) — un tableau (backlog → doing → review → done) dont les cartes sont dispatchées vers une lane `solve` ou `crew` ; `kanban learn` transforme les tâches récurrentes en cartes.
- **Loop Engineering** (`chimera workflow`) — rédigez une boucle autonome en YAML (étapes qui `utilisent` la stack, avec conditions `when` et boucles `repeat`/`until`).
- **Interfaces** — un REPL `chat`, une **TUI** plein écran (Textual) et une **passerelle de messagerie** (HTTP, ou **Discord/Telegram/Slack natif** via `serve --discord|--telegram|--slack`) avec une conversation (et mémoire) par chat ; l'agent peut aussi **envoyer** des messages via l'outil `send_message`.
- **Sandbox d'exécution** — exécutez l'outil shell localement ou dans un conteneur **Docker** isolé (`CHIMERA_SANDBOX=docker`).
- **Intégrations** — un client **MCP** (stdio) first-class + un importateur **OpenAPI/REST → tool** ; des **crons** (assignés et auto-appris, avec confirmation) ; la **migration** de config/skills/mémoire long terme depuis Hermes Agent / OpenClaw.

**Extras intégrés**
- **Vision** (coller une image), **Mode Livrable** (artefacts soignés) et un **Pet** compagnon — plus des emplacements d'identifiants pré-configurés pour la recherche web, la génération d'images, le TTS/voix et plus (`chimera features`).

## Démarrage rapide

Nécessite Python **3.11+** (3.12+ recommandé) et [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # définissez au moins une clé de fournisseur (OpenRouter recommandé)
uv run chimera doctor       # vérifiez votre environnement
```

## Commandes

```bash
chimera doctor / models / features    # statut, configuration, capacités optionnelles
chimera chat                          # assistant interactif multi-tours (votre bras droit)
chimera tui                           # app terminal plein écran (Textual)
chimera serve [--discord|--telegram|--slack]  # passerelle de messagerie : HTTP, ou bot de plateforme natif
chimera run "PROMPT" --image pic.png   # Tier-1 en un coup (avec vision via --image)
chimera deliver "un plan" -o plan.md   # Mode Livrable : produit un artefact soigné
chimera fuse "PROMPT" --show-panel     # LLM-Fusion : panel -> juge -> synthétiseur
chimera solve "TÂCHE" --verify "pytest -q" --rubric --isolate   # Tier-2 : verify-or-revert (+ revue par rubrique), isolé en git worktree
chimera lifecycle "TÂCHE" --verify "..."   # crew SDLC : plan -> build -> test -> review
chimera workflow flow.yaml             # exécute une boucle déclarative (Loop Engineering)
chimera crew "TÂCHE" --mode supervisor  # crew multi-agents Tier-3
chimera meta "un agent pour X"          # méta-agent Tier-4 : conçoit un agent spécialisé
chimera kanban add/board/run/learn     # tableau de tâches avec worker lanes (solve/crew)
chimera drift spec.yaml                # gate de drift spec<->code (sort 1 en cas de drift)
chimera memory add / graph             # mémoire long terme curatée + graphe entité-relation
chimera cron add / learn               # tâches planifiées (assignées + auto-apprises, confirmées)
chimera bench                          # benchmark d'évolution continue
chimera migrate hermes ~/.hermes --apply   # importe config + skills + fusionne la mémoire
chimera evolve status / tune / recipe   # évolution opt-in : méta-recherche de spec (tune), données SFT/DPO + recette LoRA
chimera pet new --name Chimi           # adoptez un compagnon virtuel
```

Consultez le **[Guide d'utilisation](docs/usage.md)** pour l'installation, la configuration et chaque commande avec des exemples à copier-coller.

## Architecture

```
chimera/
  core/          boucle de l'agent (ReAct) + autonomie Tier-2 (plan, verify-or-revert) + isolement git worktree
  fusion/        panel -> juge -> synthétiseur + routeur conscient du coût
  memory/        working / episodic / semantic / persona + couche graph + Memory Manager
  skills/        bibliothèque intégrée + récupération de skill-context
  evolution/     évolueur de skills, hook d'auto-évolution, buffer d'expérience
  governance/    noyau de confiance (règles + juge + précédent gardé), validateur statique, gate de drift, modèle à 4 acteurs, audit
  orchestration/ rôles, crews séquentielles/superviseur, comms MOC, crew de cycle de vie SDLC
  ecosystem/     méta-agent, gouvernance du rythme de changement, collecte de trajectoires, évolution de modèle
  kanban/        tableau de tâches + worker lanes (dispatch vers crews / solve)
  workflow/      DSL déclaratif de boucles (Loop Engineering)
  tools/         outils natifs (fichiers, shell, http)
  sandbox/       backends d'exécution (local / docker isolé)
  integrations/  client MCP (stdio) + importateur OpenAPI->tool
  scheduler/     crons (assignés + auto-appris) + moteur SOP
  migration/     import depuis Hermes/OpenClaw (config, skills, fusion de mémoire)
  providers/     passerelle LLM (LiteLLM) — fallback, credential pools, endpoints personnalisés, prompt cache
  interface/     ChatSession conversationnelle (partagée par chat, TUI, passerelle)
  tui/  server/   app Textual plein écran · passerelle de messagerie + transport HTTP
  eval/          évolution continue + stress test EvoClaw + scénarios quotidiens
  cli/           la commande `chimera` (CLI-first)
```

Consultez [docs/architecture.md](docs/architecture.md) pour la conception complète et la recherche sur laquelle elle s'appuie.

## Feuille de route

| Jalon | Statut |
|---|---|
| M0–M7 — Tiers 1–4 + Fusion + auto-évolution + gouvernance | ✅ |
| M8 — Interfaces (chat/TUI/passerelle), stress-test EvoClaw, évolution de modèle opt-in | ✅ |
| Couche fournisseurs — endpoints auto-hébergés, fallback, credential pools, `/model`, prompt cache | ✅ |
| Boucle comportementale fermée — expérience→planner, auto-mémoire, auto-skill (gouverné) | ✅ |
| Orchestration opérationnelle — Kanban + worker lanes, crew SDLC, Loop DSL | ✅ |
| Isolement d'exécution — sandbox Docker + git worktrees | ✅ |
| Techniques des papers — HORIZON · VIBEMed · Spec Growth · AgentTrust v2 · AutoMegaKernel · Meta-Agent · MOC | ✅ |
| Techniques des papers (II) — MemGate · valeur mémoire multi-facteurs · Data Recipes · OpenClaw-Skill · SkillAdaptor · DailyReport · méta-recherche de spec OpenJarvis | ✅ |

Ensuite : validation d'évolution continue plus poussée à l'échelle, connexions OAuth de fournisseurs et un
backend de durabilité LangGraph optionnel. L'entraînement de modèle (LoRA/DPO) reste externe/opt-in par conception.

## Développement

```bash
uv run ruff check .      # lint
uv run mypy chimera      # vérification de types (strict)
uv run pytest -q         # tests
```

Consultez [CONTRIBUTING.md](CONTRIBUTING.md) et [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Problèmes de sécurité : voir [SECURITY.md](SECURITY.md).

## Communauté

Rejoignez la conversation sur **[Discord](https://discord.gg/ACvBbrmguV)** — questions, idées et contributions bienvenues.

## Licence

[Apache-2.0](LICENSE).
