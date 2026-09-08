---
source_sha256: 16ac522d3f1fdc1a508d268b9f88b22f75c896bd1ae6dd87839afd256e4d745f
---

# Chimera

Un agent IA open-source (Apache-2.0), auto-évolutif, dont le cœur de raisonnement **fusionne
plusieurs modèles** (panel → juge → synthétiseur) derrière un routeur sensible au coût — avec
un noyau de gouvernance, un sandbox, et une mémoire qui apprend.

Ce site est orienté tâches : choisissez ce que vous voulez faire.

<div class="grid cards" markdown>

- **:material-rocket-launch: Démarrer**
  Installez, ajoutez une clé, lancez votre première tâche en cinq minutes.
  [Installation & premier lancement →](usage.md)

- **:material-toolbox: Faire quelque chose de concret**
  Des recettes exécutables : triage d'e-mails, brief de recherche quotidien, surveillance de dépôt.
  [Recettes →](recipes.md)

- **:material-power-plug: Connecter des outils**
  Branchez n'importe quel serveur MCP (GitHub, système de fichiers, …).
  [Serveurs MCP →](mcp.md)

- **:material-account-switch: Piloter un autre agent**
  Confiez un tour à Claude Code ou Gemini CLI via ACP — et lisez ce que cela fait aux garanties.
  [Agents externes →](external-agents.md)

- **:material-server: L'exploiter**
  Faites-le tourner 24/7 sur un petit serveur ; planifiez des tâches ; livrez dans un chat.
  [Déployer →](deploy.md)

- **:material-shield-lock: Sécurité**
  Gouvernance, sandbox, suivi de la contamination (taint tracking) — et leurs limites honnêtes.
  [Sécurité →](security.md) · [Audit des canaux dormants →](../../audits/sleeper-channels.md)

- **:material-sitemap: Comprendre**
  Comment le cœur de fusion, l'évolution et les couches de sécurité s'articulent.
  [Architecture →](architecture.md)

- **:material-console: Toutes les commandes**
  Les 79, générées depuis la CLI elle-même — dont les 33 qui n'apparaissaient dans aucune
  documentation.
  [Référence des commandes →](../../commands.md)

</div>

## La commande d'une ligne

```bash
uv sync --extra dev && uv run chimera init
```

Essayez ensuite `chimera run "..."`, ou une vraie recette :

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## Honnête par défaut

Chimera est en **alpha**. Il livre une défense en profondeur, mais la documentation dit
clairement où chaque garde-fou s'arrête — les défenses contre l'injection publient même un
chiffre mesuré (`chimera redteam`). Voir [Sécurité](security.md).
