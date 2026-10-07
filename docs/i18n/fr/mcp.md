---
source_sha256: a4a013be96904287074cc9859aaa710195511afc3c9b81627392342b7ef34e27
---

# Connecter des serveurs MCP

MCP (Model Context Protocol) est la manière standard de brancher des outils externes sur un
agent — GitHub, systèmes de fichiers, Notion, bases de données, et des centaines d'autres
serveurs le parlent. Chimera a un client MCP de premier ordre : les outils de n'importe quel
serveur deviennent des outils Chimera ordinaires, logés dans le même registre que les outils
intégrés, gouvernés par les mêmes couches liste blanche/noyau/registre.

## HTTP streamable distant

Pour un endpoint MCP qui parle streamable HTTP, configurez `url` au lieu de `command`. La CLI
accepte `chimera mcp add NAME --url https://host.example/mcp` ; authentifiez-vous avec
`--token-env ENVIRONMENT_VARIABLE` pour résoudre un bearer token à l'exécution sans enregistrer sa
valeur dans `mcp.json`.

Pour OAuth authorization-code + PKCE, configurez `oauth_authorization_url`, `oauth_token_url` et
`oauth_client_id` dans `mcp.json` (ou utilisez les options correspondantes de `chimera mcp add`).
Chimera ouvre la page d'autorisation, reçoit le callback en loopback, échange le code avec son
vérificateur PKCE et stocke le token obtenu dans le coffre d'identifiants du système
d'exploitation. Le token stocké n'est envoyé que sous forme d'en-tête `Authorization: Bearer` et
n'est jamais journalisé. Installez l'extra optionnel `secrets` pour la prise en charge du coffre du
système ; la configuration OAuth échoue en position fermée si aucun coffre n'est disponible.

La connexion ne s'exécute qu'à partir d'un Test explicite (`chimera mcp test NAME` ou le bouton
Test de l'écran). Au démarrage, le pool et l'autoload n'ouvrent jamais de navigateur : un serveur
sans token stocké est ignoré et le journal indique de lancer le Test. Un identifiant (bearer token
ou échange OAuth) n'est jamais envoyé qu'en https, ou en http simple vers loopback ; une URL
`http://` distante avec un token est refusée.

Les serveurs distants passent par la même interface configurée d'outils MCP, le même espace de noms
du registre, le même pool de longue durée, la même commande de sonde, la même gestion d'erreurs et
la même clôture d'observation que les serveurs stdio. Traitez les métadonnées et les résultats des
outils distants comme du contenu serveur non fiable.

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
- **`instructions` du serveur** — le texte qu'un serveur renvoie à `initialize` est écarté, par
  décision : c'est du texte de serveur non fiable, et rien ne le marque comme donnée comme l'est une
  lecture clôturée. Le prix est que les consignes d'usage d'un serveur n'atteignent jamais le modèle
  ; un hôte qui les transmet ne devrait pas non plus compter dessus (arXiv 2608.08467 : avec un
  outil de recherche disponible, 9 modèles sur 24 sont tombés sous 15 % sur des recherches placées
  dans les instructions du serveur). Les transmettre comme données clôturées sous taint reste
  ouvert, pas fait. Les écarter n'est **pas** une frontière contre le texte écrit par le serveur : les
  noms et descriptions d'outils du même serveur atteignent le modèle tels que le serveur les a
  écrits, sans clôture ; un serveur que vous connectez est un serveur dont le modèle lit les mots.

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

## Laisser Claude piloter l'app de bureau

`chimera serve --mcp` construit son propre agent. `chimera mcp desktop` ne construit rien : c'est
une télécommande pour l'app de bureau **que vous avez déjà ouverte**, donc Claude voit les mêmes
conversations, exécutions et approbations que vous, et ce qu'il lance tourne sous la gouvernance
de l'app, sur ses écrans.

1. Dans l'app, ouvrez **Réglages → Claude** et activez **Autoriser Claude à piloter cette app**.
2. Enregistrez le serveur dans Claude Code (ou ajoutez la même commande à la config de Claude
   Desktop) :

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

Avec le premier interrupteur activé, Claude peut lire et lancer des conversations
(`desktop_send`), des exécutions, des lots, des tableaux et des tâches cron, chercher et modifier
la mémoire, et lire les fichiers et l'état git. Une exécution qu'il lance porte la posture que
vous avez configurée, et une requête qui tente de l'élargir — une commande `verify`, l'exécution
sur l'hôte, un autre agent, l'auto-approbation — est refusée. Les approbations restent les
vôtres : `desktop_approvals` ne fait que les lister. Quand un tour s'arrête sur l'une d'elles,
`desktop_send` répond aussitôt qu'il vous attend, et le tour continue dans l'app ; `desktop_job`
indique comment il se termine.

Le second interrupteur, **Contrôle total**, ajoute `desktop_approve` (répondre aux approbations et
aux étapes soumises à validation) et `desktop_settings` (modifier les réglages et l'identité de
l'agent). Activé, il permet à Claude d'approuver des actions sans vous — et une page ou un message
piégé par injection de prompt, lu par l'agent, pourrait l'y amener. Ces deux outils ne sont pas
listés tant qu'il est désactivé, et l'app les refuse s'ils sont appelés malgré tout.

Certaines décisions restent les vôtres, quel que soit l'interrupteur. Quel modèle répond — chaque
réglage de modèle, la chaîne de repli, le panel, le juge et le synthétiseur de la fusion, le mode
de coût, la cascade et les réponses vérifiées — et si l'app exécute les tâches planifiées, Claude
peut seulement le *suggérer* : rien n'est écrit, et l'app vous montre une carte avec la valeur
actuelle et la valeur proposée de chaque réglage, à approuver ou refuser sur place. Claude ne peut
répondre à cette carte par aucune voie ; si le réglage a changé avant votre approbation, rien
n'est appliqué. Et autoriser les commandes dans un dossier, lancer une commande dans le Runner,
démarrer un bot de messagerie et enregistrer un agent avec ses droits d'outils sont refusés
entièrement par le pont : vous les faites dans l'app.

Et une exécution lancée par Claude, quel que soit l'interrupteur, utilise les modèles que vous
avez configurés et ne va pas plus loin que la posture que vous avez configurée. Une demande qui
nomme un modèle, un plan de rôles, un profil, un panel de fusion ou un autre agent est refusée ; de
même une posture plus large — plus de portée, des approbations plus lâches, l'exécution sur l'hôte
ou une commande `verify` là où vous n'avez accordé aucun shell, ou l'approbation automatique.
Demander à une exécution d'en faire moins (lecture seule, ou approbation toujours) est permis.

Ce qu'aucun interrupteur ne permet : lire ou écrire une clé d'API, un jeton ou un webhook. Les
modifications de réglages refusent les noms d'identifiants, les routes qui portent des clés ou
des liens de partage sont inaccessibles, les fichiers d'identifiants (`.env`, clés privées) ne
peuvent être ni lus, ni écrits, ni cherchés, et chaque résultat est nettoyé des valeurs
d'identifiants. On ne peut pas non plus pointer un workspace vers le dossier de données de l'app,
ni vers un dossier qui le contient (votre dossier personnel, par exemple) : c'est là que sont
gardées les réponses aux approbations, et un fichier écrit là en validerait une.

Comment ça se connecte : tant que l'interrupteur est activé, l'app écrit
`~/.chimera/desktop-bridge.json` (l'URL de son API en loopback et un jeton aléatoire ; lisible
par vous seul sous POSIX, dans votre profil sous Windows). Désactiver l'interrupteur ou fermer
l'app le supprime et retire le jeton. App fermée, chaque outil répond « Chimera desktop is not
running, or 'Allow Claude to operate this app' is off in Settings. » Claude liste les outils à la
connexion ; après avoir activé ou désactivé **Contrôle total**, reconnectez le serveur (`/mcp`
dans Claude Code) pour voir la nouvelle liste.

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
