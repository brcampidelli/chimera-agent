<div align="center">

<img src="assets/logo-wide.png" alt="Logo Chimera" width="460" />

# Chimera

**Un agent IA open-source et auto-évolutif dont le cœur de raisonnement est un moteur de Fusion de LLM.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-rejoindre-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <b>Français</b> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

Chimera fusionne **plusieurs LLM par requête** — un pipeline **panel → juge → synthétiseur**,
inspiré d'OpenRouter Fusion — au lieu de s'appuyer sur un seul modèle de pointe, et
**s'améliore au fil du temps** (mémoire → compétences → modèle), résistant à la *dégradation par
évolution continue* qui limite les agents actuels.

> **Statut :** alpha précoce. Les 8 jalons du plan de construction (M0–M7) sont implémentés :
> Tiers 1–4 + le moteur de Fusion + auto-évolution + un noyau de gouvernance.
> 158 tests · `mypy --strict` propre · `ruff` propre.

---

## Pourquoi Chimera

Chaque framework existant est fort sur **un seul axe** : Hermes/OpenClaw font évoluer des
compétences mais utilisent un seul modèle ; CrewAI/LangGraph orchestrent bien mais n'apprennent
pas ; TrustClaw/NemoClaw/ZeroClaw apportent sécurité/sandbox mais n'évoluent pas.
**Chimera combine les quatre :**

- 🧬 **La fusion comme raisonnement** — le moteur panel→juge→synthétiseur est le cœur de raisonnement, pas un module additionnel. Le gain vient de la *synthèse* elle-même, pas seulement de la diversité des modèles.
- 🪜 **Quatre niveaux de capacité en une seule progression** — outils augmentés → autonome sur tâche unique → équipes multi-agents → écosystème auto-évolutif.
- ♻️ **Auto-évolution multi-niveaux** qui s'attaque explicitement à la dégradation par évolution continue (état externalisé, contexte résistant à la dérive, verify-or-revert, tampon d'expérience).
- 🛡️ **Un noyau de gouvernance qui s'améliore aussi** — allow/warn/block/review, avec une surface d'auto-modification validée statiquement.

## Fonctionnalités

- **Moteur de Fusion de LLM** — panel agnostique au fournisseur de modèles de pointe + ouverts, un juge qui révèle consensus/contradictions/angles morts, et un synthétiseur ; un **routeur sensible au coût** ne fusionne que lorsque c'est rentable (les tours avec outils restent sur un seul modèle).
- **Autonomie Tier-2** — planifier → exécuter → revue du Manager → **verify-or-revert** (snapshot/restore du workspace + vérificateur par commande), avec un tampon d'expérience façon git.
- **Auto-évolution** — un Memory Manager (dédup ADD/UPDATE/DELETE/NOOP), un évolueur de compétences qui *écrit et teste ses propres compétences* (proposer → tester → garder/écarter), des crons auto-appris et un **benchmark d'évolution continue** qui mesure la dégradation.
- **Équipes multi-agents** — spécialisation par rôles, crews séquentielle et superviseur, consolidation de messages MOC, mémoire partagée, revue en parallèle.
- **Gouvernance et sécurité** — un noyau de confiance auto-évolutif, un validateur statique pour la surface d'auto-modification, un journal d'audit en ajout seul et des outils gouvernés.
- **Intégrations** — client **MCP** de premier ordre + un importateur **OpenAPI/REST → outil**, pour ajouter n'importe quelle plateforme ou API.
- **Crons et proactivité** — tâches planifiées assignées par des humains et auto-apprises.
- **Migration** — importez config, compétences et **mémoire à long terme** depuis Hermes Agent / OpenClaw (la mémoire est *fusionnée*, jamais écrasée).
- **CLI-first** — tout fonctionne depuis le terminal ; agnostique au fournisseur via LiteLLM/OpenRouter.

## Démarrage rapide

Nécessite Python **3.11+** (3.12+ recommandé) et [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # définissez au moins une clé de fournisseur (OpenRouter recommandé)
uv run chimera doctor       # vérifiez votre environnement
```

## Commandes

```bash
chimera doctor / models               # statut et configuration
chimera run "PROMPT"                   # complétion Tier-1 en une fois
chimera fuse "PROMPT" --show-panel     # Fusion de LLM : panel -> juge -> synthétiseur
chimera agent "TASK" --fuse --guard    # boucle d'agent ReAct (tool calls gouvernés)
chimera solve "TASK" --verify "pytest -q"   # Tier-2 autonome : planifier -> verify-or-revert
chimera crew "TASK" --mode supervisor  # crew multi-agents Tier-3
chimera meta "an agent for X"          # méta-agent Tier-4 : conçoit un agent spécialisé
chimera memory add "un fait durable"   # mémoire à long terme curée (dédupliquée)
chimera cron add NAME "0 9 * * *" "run report"   # planifie une tâche
chimera cron learn                     # propose des crons à partir de tâches récurrentes (désactivés)
chimera bench                          # benchmark d'évolution continue
chimera guard "rm -rf /"               # prévisualise un verdict de gouvernance
chimera migrate hermes ~/.hermes --apply   # importe config + compétences + fusionne la mémoire
```

## Architecture

```
chimera/
  core/          boucle d'agent (ReAct) + autonomie Tier-2 (plan, verify-or-revert, superviseur)
  fusion/        panel -> juge -> synthétiseur + routeur sensible au coût
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        bibliothèque intégrée + récupération de skill-context
  evolution/     évolueur de compétences apprises, tampon d'expérience
  governance/    noyau de confiance (allow/warn/block/review), validateur statique, audit, outils gouvernés
  orchestration/ rôles, crews séquentielle & superviseur, comms MOC
  ecosystem/     méta-agent, gouvernance du rythme de changement, collecte de trajectoires
  tools/         outils natifs (fichiers, shell, http)
  integrations/  client MCP + importateur OpenAPI->outil
  scheduler/     crons (assignés + auto-appris) + moteur de SOP
  migration/     import depuis Hermes/OpenClaw (config, compétences, fusion de mémoire)
  providers/     adaptateurs LLM (LiteLLM / OpenRouter)
  eval/          benchmark d'évolution continue, tâches démo
  cli/           la commande `chimera` (CLI-first)
```

Voir [docs/architecture.md](docs/architecture.md) pour la conception complète et la recherche sous-jacente.

## Feuille de route

| Jalon | Statut |
|---|---|
| M0 — Fondations (gateway, config, CLI) | ✅ |
| M1 — Tier 1 + outils/compétences/intégrations/crons/migration | ✅ |
| M2 — Moteur de Fusion de LLM + routeur sensible au coût | ✅ |
| M3 — Tier 2 autonome (verify-or-revert) | ✅ |
| M4 — Auto-évolution (mémoire, compétences, crons appris, benchmark) | ✅ |
| M5 — Noyau de gouvernance | ✅ |
| M6 — Équipes multi-agents Tier 3 | ✅ |
| M7 — Écosystème auto-évolutif Tier 4 | ✅ |

Ensuite : validation avec de vrais modèles à l'échelle, une suite d'évolution continue étendue et un backend de durabilité optionnel avec LangGraph.

## Développement

```bash
uv run ruff check .      # lint
uv run mypy chimera      # vérification de types (strict)
uv run pytest -q         # tests
```

Voir [CONTRIBUTING.md](CONTRIBUTING.md) et [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Problèmes de sécurité : voir [SECURITY.md](SECURITY.md).

## Communauté

Rejoignez la conversation sur **[Discord](https://discord.gg/ACvBbrmguV)** — questions, idées et contributions bienvenues.

## Licence

[Apache-2.0](LICENSE).
