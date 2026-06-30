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
> implémenté — Tiers 1–4 + le moteur de Fusion + auto-évolution + un noyau de gouvernance — plus
> une **couche d'interfaces** (chat, TUI, passerelle HTTP), une **évolution de modèle opt-in** et
> une **couche de fonctionnalités** (Vision, Mode Livrable, Pets, …).
> 224 tests (+ intégration en direct opt-in) · `mypy --strict` propre · `ruff` propre.

---

## Pourquoi Chimera

Les frameworks existants sont forts sur un seul axe : Hermes/OpenClaw font évoluer les skills mais
utilisent un seul modèle ; CrewAI/LangGraph orchestrent bien mais n'apprennent pas ;
TrustClaw/NemoClaw/ZeroClaw apportent sécurité/sandbox mais n'évoluent pas. **Chimera combine les quatre :**

- 🧬 **La Fusion comme raisonnement** — le moteur panel→juge→synthétiseur est le cœur de raisonnement, pas un module additionnel. Le gain vient de l'étape de *synthèse* elle-même, pas seulement de la diversité des modèles.
- 🪜 **Quatre niveaux de capacité dans une progression** — outils augmentés → autonome mono-tâche → équipes multi-agents → écosystème auto-évolutif.
- ♻️ **Auto-évolution multi-niveaux** qui attaque explicitement la dégradation par évolution continue (état externalisé, contexte résistant au drift, verify-or-revert, buffer d'expérience).
- 🛡️ **Un noyau de gouvernance qui s'améliore aussi** — allow/warn/block/review, avec une surface d'auto-modification validée statiquement.

## Fonctionnalités

**Raisonnement & autonomie**
- **Moteur LLM-Fusion** — panel agnostique de fournisseur de modèles de pointe + ouverts, un juge qui fait ressortir consensus/contradictions/angles morts, et un synthétiseur ; un **routeur conscient du coût** ne fusionne que lorsque c'est rentable (les tours d'outils restent en modèle unique).
- **Autonomie Tier-2** — planifier → exécuter → revue du Manager → **verify-or-revert** (snapshot/restauration du workspace + un vérificateur par commande), avec un buffer d'expérience à la git.
- **Équipes multi-agents** — spécialisation par rôles, crews séquentielles et superviseur, consolidation de messages MOC, mémoire partagée, revue parallèle.

**Auto-évolution & gouvernance**
- **Auto-évolution** — un Memory Manager (déduplication ADD/UPDATE/DELETE/NOOP), un évolueur de skills qui *écrit et teste ses propres skills* (proposer → tester → garder/écarter), des crons auto-appris et un **benchmark d'évolution continue** (plus un stress test EvoClaw naive-vs-guarded) qui mesure la dégradation.
- **Évolution de modèle opt-in** — `solve` collecte des trajectoires ; `evolve` les transforme en datasets SFT/DPO et émet une recette LoRA exécutable. L'entraînement reste **externe et opt-in** — jamais automatique.
- **Gouvernance & sécurité** — un noyau de confiance qui s'améliore (allow/warn/block/review), un validateur statique pour la surface d'auto-modification, un journal d'audit append-only et des outils gouvernés.

**Fournisseurs**
- **N'importe quel modèle, une interface** — agnostique de fournisseur via LiteLLM (100+ modèles via des slugs `provider/model`) ; clés first-class pour OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Auto-hébergé & résilient** — endpoints personnalisés pour **Ollama/vLLM** (`CHIMERA_API_BASE`), **chaînes de fallback** entre modèles, **credential pools** avec rotation round-robin des clés, et un changement de modèle **`/model`** en direct dans `chat`/`tui`.

**Interfaces & intégrations**
- **CLI-first, plus des interfaces** — un REPL `chat`, une **TUI** plein écran (Textual) et une **passerelle de messagerie** HTTP avec une conversation (et mémoire) par chat.
- **Intégrations** — un client **MCP** (stdio) first-class + un importateur **OpenAPI/REST → tool**, pour ajouter n'importe quelle plateforme ou API.
- **Crons & proactivité** — tâches planifiées assignées par des humains et auto-apprises.
- **Migration** — importe config, skills et **mémoire long terme** depuis Hermes Agent / OpenClaw (la mémoire est *fusionnée*, jamais écrasée).

**Extras intégrés**
- **Vision** (coller une image), **Mode Livrable** (produit des artefacts soignés et autonomes) et un **Pet** compagnon — plus des emplacements d'identifiants pré-configurés pour la recherche web, la génération d'images, le TTS/voix et plus (`chimera features` montre ce qui est prêt et ce dont chacun a besoin).

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
chimera serve                         # serveur HTTP de la passerelle de messagerie (sessions par chat)
chimera run "PROMPT" --image pic.png   # Tier-1 en un coup (avec vision via --image)
chimera deliver "un plan de lancement" -o plan.md   # Mode Livrable : produit un artefact soigné
chimera fuse "PROMPT" --show-panel     # LLM-Fusion : panel -> juge -> synthétiseur
chimera agent "TÂCHE" --fuse --guard    # boucle d'outils ReAct (appels gouvernés)
chimera solve "TÂCHE" --verify "pytest -q"   # Tier-2 autonome : planifier -> verify-or-revert
chimera crew "TÂCHE" --mode supervisor  # crew multi-agents Tier-3
chimera meta "un agent pour X"          # méta-agent Tier-4 : conçoit un agent spécialisé
chimera memory add "un fait durable"    # mémoire long terme curatée (dédupliquée)
chimera cron add NOM "0 9 * * *" "exécuter rapport"   # planifie une tâche
chimera cron learn                     # propose des crons à partir de tâches récurrentes (désactivé)
chimera bench                          # benchmark d'évolution continue
chimera guard "rm -rf /"               # aperçu d'un verdict de gouvernance
chimera migrate hermes ~/.hermes --apply   # importe config + skills + fusionne la mémoire
chimera evolve status / recipe             # évolution de modèle opt-in : données SFT/DPO + recette LoRA
chimera pet new --name Chimi               # adoptez un compagnon virtuel (les stats décroissent avec le temps)
```

Consultez le **[Guide d'utilisation](docs/usage.md)** pour l'installation, la configuration et chaque commande avec des exemples à copier-coller.

## Architecture

```
chimera/
  core/          boucle de l'agent (ReAct) + autonomie Tier-2 (plan, verify-or-revert, superviseur)
  fusion/        panel -> juge -> synthétiseur + routeur conscient du coût
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        bibliothèque intégrée + récupération de skill-context
  evolution/     évolueur de skills apprises, buffer d'expérience
  governance/    noyau de confiance (allow/warn/block/review), validateur statique, audit, outils gouvernés
  orchestration/ rôles, crews séquentielles et superviseur, comms MOC
  ecosystem/     méta-agent, gouvernance du rythme de changement, collecte de trajectoires, évolution de modèle
  tools/         outils natifs (fichiers, shell, http)
  integrations/  client MCP (stdio) + importateur OpenAPI->tool
  scheduler/     crons (assignés + auto-appris) + moteur SOP
  migration/     import depuis Hermes/OpenClaw (config, skills, fusion de mémoire)
  providers/     passerelle LLM (LiteLLM) — chaînes de fallback, credential pools, endpoints personnalisés
  interface/     ChatSession conversationnelle (partagée par chat, TUI, passerelle)
  tui/           app Textual plein écran
  server/        passerelle de messagerie + transport HTTP (sessions par chat)
  eval/          évolution continue + stress test EvoClaw + scénarios quotidiens
  cli/           la commande `chimera` (CLI-first)
```

Consultez [docs/architecture.md](docs/architecture.md) pour la conception complète et la recherche sur laquelle elle s'appuie.

## Feuille de route

| Jalon | Statut |
|---|---|
| M0 — Fondations (gateway, config, CLI) | ✅ |
| M1 — Tier 1 + outils/skills/intégrations/crons/migration | ✅ |
| M2 — Moteur LLM-Fusion + routeur conscient du coût | ✅ |
| M3 — Tier 2 autonome (verify-or-revert) | ✅ |
| M4 — Auto-évolution (mémoire, skills, crons appris, benchmark) | ✅ |
| M5 — Noyau de gouvernance | ✅ |
| M6 — Équipes multi-agents Tier 3 | ✅ |
| M7 — Écosystème auto-évolutif Tier 4 | ✅ |
| M8 — Interfaces (chat/TUI/passerelle), stress-test EvoClaw, évolution de modèle opt-in | ✅ |
| Couche fournisseurs — endpoints auto-hébergés, chaînes de fallback, credential pools, `/model` | ✅ |
| Fonctionnalités — Vision, Mode Livrable, Pets + emplacements de capacités pré-configurés | ✅ |

Après M7, l'agent a été durci face à de vrais modèles de fournisseur (testé en direct : Fusion,
`solve` Tier-2, la suite de scénarios quotidiens, la passerelle HTTP, l'importateur OpenAPI et le
client MCP stdio). Ensuite : validation d'évolution continue plus poussée à l'échelle, plus
d'intégrations de fournisseurs (connexions OAuth, réglage des credential pools) et un backend de
durabilité LangGraph optionnel.

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
