---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
---

# Connecter des serveurs MCP

MCP (Model Context Protocol) est la manière standard de brancher des outils externes sur un
agent — GitHub, systèmes de fichiers, Notion, bases de données, et des centaines d'autres
serveurs le parlent. Chimera a un client MCP de premier ordre : les outils de n'importe quel
serveur deviennent des outils Chimera ordinaires, logés dans le même registre que les outils
intégrés, gouvernés par les mêmes couches liste blanche/noyau/registre.

## Installer l'extra client

Le client MCP vit derrière un extra optionnel pour que le cœur reste léger :

```bash
uv sync --extra mcp
```

La plupart des serveurs sont des paquets Node, vous avez donc aussi besoin de `npx` (fourni
avec Node.js).

## Test de fumée de 60 secondes (sans identifiants)

Le serveur de système de fichiers de référence ne nécessite aucun jeton — il expose simplement
des outils de lecture/écriture sur un répertoire de votre choix :

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

Confiez ce registre à un `Agent` (ou voir `examples/mcp_github.py` pour la boucle complète) et
le modèle peut désormais appeler les outils du serveur comme n'importe quel autre.

## Un vrai serveur : GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

C'est toute l'intégration : ~26 outils GitHub (rechercher des dépôts, lire des fichiers, lister
des issues, créer des PR, ...) apparaissent dans le registre. Version exécutable de bout en
bout :
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## Comment ça s'articule avec les couches de sécurité

Les outils MCP sont des objets `Tool` ordinaires, donc tout se compose :

- **Liste blanche par session** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  n'accorde que les outils MCP dont ce run a besoin ; ceux non accordés n'atteignent jamais le
  modèle.
- **Noyau de gouvernance** — `govern_registry(...)` filtre les appels MCP allow/warn/review/block
  comme n'importe quelle commande shell.
- **Registre de contamination (ledger)** — encapsulez avec `ledger_registry(...)` et les
  récupérations MCP sont enregistrées ; notez que seuls les outils nommés dans `FETCH_TOOLS` sont
  aujourd'hui auto-classifiés, donc traitez le contenu MCP comme non fiable et préférez tourner
  avec la sémantique `--taint --guard` quand le serveur récupère des données externes.

## Chimera *en tant que* serveur MCP

Le client ci-dessus permet à Chimera d'appeler d'autres outils. L'inverse fonctionne aussi :
exécutez Chimera **en tant que** serveur MCP pour que n'importe quel client MCP — Claude
Desktop, un IDE, un autre agent — puisse appeler le moteur entier comme trois outils.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

Il expose :

| Outil | Ce qu'il fait |
| --- | --- |
| `chimera_solve` | Résout une tâche de manière autonome avec plan + verify-or-revert ; renvoie la réponse. |
| `chimera_fuse` | Répond à un prompt via le moteur LLM-Fusion (panel → juge → synthétiseur). |
| `chimera_memory_search` | Recherche dans la mémoire à long terme de Chimera et renvoie les faits principaux. |

Pointez un client MCP dessus comme serveur stdio. Pour Claude Desktop, ajoutez à sa
configuration :

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` a besoin d'une clé de fournisseur pour `chimera_solve`/`chimera_fuse` (la recherche en
mémoire fonctionne sans). Ajoutez `--fuse` pour router les tours profonds du solveur à travers
la fusion, `--no-memory` pour sauter le rappel. Comme stdio est le fil de transport, tous les
logs vont vers stderr — stdout ne porte que le protocole.

## Parler A2A (agent → agent)

MCP connecte des agents à des *outils* ; **A2A** (Agent2Agent, Linux Foundation) connecte des
agents *entre eux* — c'est natif dans LangGraph, CrewAI, et AutoGen. Chimera le parle aussi,
pour qu'un orchestrateur LangGraph/CrewAI puisse déléguer une tâche à Chimera et récupérer un
résultat terminé.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` ajoute deux routes au serveur HTTP :

| Route | Objectif |
| --- | --- |
| `GET /.well-known/agent.json` | L'Agent Card — identité + skills annoncées (solve, fuse). |
| `POST /a2a` | Cycle de vie de tâche JSON-RPC 2.0 : `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

Un client envoie `message/send` avec une partie textuelle ; Chimera exécute l'agent autonome et
renvoie une tâche `completed` (ou `failed`) portant la réponse comme message d'agent. Ou il
envoie `message/stream` et obtient un flux **Server-Sent Events** : la tâche en état `working`
d'abord, puis la tâche `completed`/`failed` une fois le run terminé — pour qu'un orchestrateur
voie la progression sans polling. L'agent card annonce `capabilities.streaming: true`.

**Portée, honnêtement :** le flux émet actuellement deux événements (working → final), pas de
deltas de tokens par étape, et les notifications push ne sont pas implémentées. C'est un flux
conforme, sans nécessité de polling — suffisant pour être un nœud streamable de premier ordre
dans une app LangGraph/CrewAI.

## Dépannage

- `TimeoutError: MCP server ... did not become ready` — la commande n'a pas démarré. Exécutez la
  même ligne `npx ...` manuellement dans un terminal pour voir son erreur (jeton manquant, Node
  manquant, téléchargement de paquet au premier lancement trop lent — augmentez
  `connect_timeout`).
- `ModuleNotFoundError: mcp` — installez l'extra : `uv sync --extra mcp`.
- Conflits de noms d'outils — passez toujours un `name_prefix`.
- La session exécute le serveur comme sous-processus pendant toute la durée de votre script ;
  appelez la méthode `close()` de la session du `connector` (ou laissez simplement le processus
  se terminer) pour le démonter.
