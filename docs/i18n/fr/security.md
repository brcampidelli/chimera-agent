---
source_sha256: 6b2e412ec468cf2e42add60b1e9ed3d6a41f4e7f0dac7b2ddea926c43584abdb
---

# Sécurité & garde-fous

Chimera peut exécuter des commandes shell, modifier des fichiers, appeler des API, et modifier
ses propres skills. Il livre une **défense en profondeur**, et — c'est important — la
documentation précise où chaque couche *s'arrête*.

!!! warning "La règle unique"
    Aucun de ces garde-fous ne remplace le fait de **l'exécuter dans un environnement isolé**
    quand vous lui accordez de l'autonomie. Le runner `local` par défaut n'est pas isolé ;
    utilisez `CHIMERA_SANDBOX=docker` (réseau désactivé, éventuellement sous gVisor) pour du
    travail non fiable.

## La voix de l'agent dans les prompts et les textes du bot

Les prompts et les modèles du bot ne doivent pas faire affirmer à l'agent des sentiments, de l'attention,
de l'amitié, une relation ou des promesses à son sujet. Dites ce qu'il a fait, ce qu'il n'a pas pu faire et
à quel point il est sûr. L'incertitude calibrée à la première personne (par exemple « je ne suis pas sûr,
mais… ») est permise et utile.

## Les couches

- **Noyau de gouvernance** — chaque appel d'outil gouverné est allow / warn / review / block.
  Un premier filtre bon marché des signatures shell dangereuses, pas la frontière.
- **Sandbox** — un conteneur éphémère sans réseau (`CHIMERA_SANDBOX=docker`), renforçable avec
  gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`). La **commande verify** s’exécute dans le même sandbox
  que le shell de l’agent. Un vérificateur déclaré dans `.chimera/verify.toml` reste une commande
  shell : sur un hôte non isolé, il ne s’exécute que si le projet dispose d’une autorisation
  `shell` active et si `CHIMERA_HOST_EXEC` n’interdit pas l’exécution sur l’hôte. Sans cette
  autorisation, il s’abstient ; les commandes déduites des fichiers de build continuent de passer
  par la confirmation `CHIMERA_HOST_EXEC` et s’abstiennent en cas de refus. Une autorisation du
  projet permet la commande configurée, mais ne rend pas le sandbox de l’hôte isolé
  (`CHIMERA_VERIFY_NETWORK=1` donne le réseau à un vérificateur Docker ; les sandboxes noyau ne le
  peuvent pas, donc un vérificateur nécessitant le réseau s’exécute sur l’hôte uniquement s’il est
  autorisé par ailleurs). Traitez la configuration et sa commande comme du contenu exécutable du
  projet ; examinez-les avant d’accorder l’accès shell. Si un tour modifie le vérificateur
  configuré, la vérification s’abstient ; la commande est capturée avant le tour et exige, avant sa
  première exécution, une approbation durable du propriétaire liée à son hash SHA-256 exact (et de
  nouveau à chaque changement). Cette approbation ne porte que sur la chaîne de commande, pas sur
  ses effets : `pytest`, `make test` et `npm test` exécutent des fichiers contrôlés par le projet,
  tels que `conftest.py`, les recettes du `Makefile` et les scripts de paquet, que l’agent peut
  également avoir modifiés.
Une clé `setup` facultative dans ce fichier désigne une commande de préparation de l’environnement du projet. Elle exige sa propre approbation du propriétaire, liée au hash exact, ainsi que la même autorisation `shell` active du projet. Elle s’exécute au plus une fois par espace de travail et hash de commande ; les échecs sont signalés à l’agent sans interrompre le tour. Les approbations et la marque de préparation sont conservées dans le répertoire personnel de Chimera, pas dans le checkout. Les autorisations et les hash approuvés ne sont hérités que par les worktrees durables créés par Chimera pour ce projet enregistré ; les worktrees Git arbitraires ne les héritent pas. Le registre des worktrees de l’application est conservé sous `CHIMERA_HOME`. Un processus disposant déjà d’un accès shell à l’hôte peut y écrire, tout comme il peut modifier le registre des projets.
- **Liste blanche d'outils par session** — n'accordez à un run que les outils dont il a besoin ;
  le reste est entièrement retiré du schéma du modèle.
- **Suivi de la contamination (taint tracking)** (`--taint`) — le contenu non fiable est clôturé
  comme donnée, sa provenance le suit jusque dans les mémoires et les skills (une skill issue
  d'un run contaminé est retenue pour révision), et une fois un run contaminé, les outils
  dangereux se restreignent.
- **Lecteur en quarantaine** — le pattern dual-LLM / CaMeL : le contenu non fiable est lu par un
  modèle sans outils qui ne peut émettre que des champs validés par schéma, si bien qu'une
  injection ne peut produire ni nouvelle instruction ni appel d'outil.
- **Moniteur inter-agents** — sous un fan-out, un moniteur par worker est aveugle à un flux
  *scindé* (un worker récupère du contenu non fiable, un autre worker l'exploite — la
  récupération et l'exploitation vivent dans des registres séparés). Un moniteur agrégé voit tout
  le fan-out ; il est **toujours actif** pour `solve-batch` / `crew-isolated`.

## Fan-out : le moniteur inter-agents

Quand plusieurs workers utilisant des outils tournent en parallèle (`solve-batch`,
`crew-isolated`), chacun reçoit son propre registre de capacités (capability ledger), et après le
batch un moniteur agrégé passe sur l'ensemble. Il détecte des schémas qu'aucun moniteur
mono-worker ne peut voir — l'exfiltration scindée où le worker A récupère du contenu non fiable
et le worker B l'exécute ou l'exfiltre :

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

Il ne fait jamais qu'**escalader vers une révision** — il ne bloque jamais un run — et c'est de
l'observabilité pure (il enregistre, ne modifie aucun comportement). Ajoutez `--taint` en plus
pour aussi armer la liste blanche adaptative de chaque worker (les outils dangereux-si-contaminé
exigent alors une approbation).

**L'approbation, et à quoi ressemble un worker refusé.** Chaque worker porte son propre
approbateur et son propre registre de ce qu'il a eu le droit de faire : une tâche refusée le
dit, au lieu de rapporter `ok` :

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

Le `ok` entre parenthèses est le verdict de la boucle elle-même, et c'est bien le désaccord
entre les deux qui compte : un appel refusé revient comme une ligne d'observation ordinaire,
le worker la lit comme n'importe quel résultat d'outil, continue, et finit en prose. Qui peut
être interrogé suit `CHIMERA_APPROVAL_MODE` — `deny` refuse tout de suite, `ask` demande sur
un terminal et sinon écrit la question pour `chimera approve` puis attend
`CHIMERA_APPROVAL_WAIT` secondes, par question et par worker. Le silence refuse toujours.

## Mesuré, pas affirmé

```bash
chimera redteam
```

fait passer un corpus d'injection dans la pile. Sur le corpus intégré, la couche de contamination
réduit le **taux de réussite des attaques de 100 % à ~14 %** — et le rapport *nomme* ce qui passe
encore (exfiltration via un outil autorisé) plutôt que de prétendre à 100 %.

La même commande imprime le **coût**, ce que la première version de cette page ne faisait pas :
sans personne à qui demander, la restriction refuse **100 % du travail légitime ayant d'abord lu
quoi que ce soit d'externe** — corriger le fichier que l'issue nomme, appliquer la mise à jour que
la documentation décrit — et la gate enregistrée (sur-blocage ≤ 5 %) échoue. Ce chiffre n'est pas un
problème de réglage ; la gate était vide. Le mode d'approbation par défaut est `ask`, et sur le
desktop il demande désormais vraiment : un appel d'outil restreint devient une question à
l'écran, accompagnée de la raison inscrite au registre, à laquelle on répond par un bouton ou
`chimera approve`, et que le silence refuse après `CHIMERA_APPROVAL_WAIT` secondes. Avec la
personne qui approuve le travail qu'elle a demandé, le sur-blocage est de 0 % et le taux de blocage
des attaques ne bouge pas — mesuré, par bras, dans
[`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md).
L'exfiltration via un outil autorisé est fermée par le même changement : le `http_get` d'un run
contaminé portant une query string est une révision, et les deux GET légitimes avec query string
ajoutés au corpus montrent ce que cela coûte.

### Mémoire empoisonnée, d'un run à l'autre

`redteam` mesure un seul run. L'autre forme est plus lente et ne tient pas dans un processus : le
run A lit une page empoisonnée et enregistre ce qu'il a « appris » ; le run B, des jours plus tard,
pose une question sans rapport et le rappel tend au modèle le fait implanté.

```bash
chimera memory-poison
```

Également hors ligne et gratuit. Il fait l'ablation des trois couches qui s'intercalent entre ces
runs — le drapeau de provenance `tainted`, la barrière d'admission du rappel, et l'étiquette
`[unverified]` que le fait porte jusque dans le prompt — parce qu'un chiffre unique serait
compatible avec le fait que n'importe laquelle d'entre elles ne serve à rien. L'essentiel, c'est ce
qui arrive **non marqué**, pas ce qui est bloqué : un fait empoisonné qui porte son origine est un
fait dont le modèle a été averti ; un fait sans étiquette est indiscernable de quelque chose que
l'agent a vérifié lui-même.

Deux résultats du premier run méritent d'être dits franchement, car aucun ne nous flatte :

- **La configuration livrée échoue à sa propre gate — sur le coût.** Elle marque 100 % du poison et
  détruit 25 % de la mémoire honnête pour y arriver. Les victimes sont nommées : un document de
  sécurité qui cite une attaque pour l'expliquer, et un ticket de support qui transmet une
  tentative. Un comparateur de motifs sur le contenu ne sait pas distinguer une citation d'une
  commande.
- **Sur ce corpus, la gate de contenu n'ajoute rien que l'étiquette de provenance ne couvre
  déjà.** Tout son effet mesuré est la mémoire honnête qu'elle supprime. Quinze lignes écrites à la
  main sont une indication et pas un verdict, et c'est pourquoi rien n'a été supprimé sur cette
  base.

Les seuils, la méthode et ce que les chiffres n'autorisent *pas* sont dans
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md),
fixés avant le premier run.

## Exposer le serveur HTTP

`chimera serve` se lie à `127.0.0.1` par défaut. Ses endpoints qui changent l'état (`/chat`,
`/a2a`, `/webhook/*`) pilotent l'agent, donc **avant d'exposer le serveur à un réseau**,
définissez un jeton bearer :

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

Une fois défini, ces endpoints POST renvoient `401` sans en-tête `Authorization: Bearer`
correspondant (`GET /health` et l'agent-card A2A restent ouverts). Pour le webhook entrant
WhatsApp, définissez `CHIMERA_WHATSAPP_APP_SECRET` avec le secret de votre app Meta — Chimera
vérifie alors le HMAC `X-Hub-Signature-256` de chaque requête et rejette une charge utile forgée
avec `403`. Les deux sont opt-in (non défini = pas d'authentification, ce qui convient en
localhost) ; un déploiement public devrait les définir (ou se placer derrière un proxy
authentifiant).

### Partager une conversation avec une seconde personne

L'application de bureau peut partager une conversation de code par un **jeton qui n'ouvre que cette
conversation** — jamais le jeton du serveur. Le propriétaire en émet un par invité
(`POST /api/code/sessions/{id}/share`) et peut le révoquer seul ; supprimer la conversation les révoque
tous. Un invité atteint quatre routes sous `/guest/api/…` : lire la conversation, la suivre en direct, y
envoyer un message, voir qui est là. Rien d'autre ne répond à un jeton de partage, et l'application invité
n'a aucune route pour répondre à une carte de gouvernance — celles-ci restent sur l'écran du propriétaire.

L'application invité est aussi la **seule** chose servie quand le propriétaire ouvre la porte réseau
(`POST /api/code/share/network`, refermée par `DELETE`, jamais ouverte au lancement) : une machine du LAN
atteint ces quatre routes et rien de ce que protège le jeton du serveur. À dire sans détour avant de
remettre un lien : un invité peut demander à l'agent tout ce que le propriétaire lui demanderait, dans le
projet du propriétaire, avec ses outils et sa dépense — sous les coutures par défaut de la requête, et
sans approuver de cartes.

### Votre propre téléphone ou navigateur : associer un appareil

Un lien invité est pour quelqu'un d'autre. Pour le propriétaire sur son propre téléphone, il y a
l'association : `chimera pair` (ou **Associer un appareil** dans Paramètres › Accès) affiche un code
valable **une fois, pendant cinq minutes**, avec un QR de l'adresse d'association. Il se saisit dans
une page servie par un second listener du LAN — fermé à chaque démarrage et ouvert seulement par le
propriétaire (`chimera pair --lan`, ou **Ouvrir** sur la carte) — et devient un cookie de 30 jours
(`HttpOnly`, `SameSite=Strict`, `Secure` en https) signé avec une clé dérivée du secret d'appareils
du propriétaire (`<home>/devices.key`). Cinq codes faux annulent tout code encore valide ; cinq
depuis une même adresse bloquent cette adresse pendant quinze minutes.

Un appareil associé est le propriétaire **pour lire et parler** : conversations, tours, consignes,
réponses aux propres questions de l'agent, et refus d'une approbation. Ce n'est pas l'écran du
propriétaire. Approuver exige toujours le code de chaque question (étude 30, S30-30), et les routes
qui distribuent des accès, modifient les paramètres, accordent des dossiers, démarrent des bots ou
arrêtent le service répondent `403` à un appareil. La carte Accès liste chaque appareil avec sa
dernière utilisation et son réseau (`192.168.1.x`) ; **Révoquer** en met fin à un immédiatement, y
compris un flux en direct qu'il a ouvert, et **Révoquer tous les appareils** remplace le secret, si
bien qu'aucun cookie émis avant ne se vérifie plus. Le pont du bureau ne peut ni associer, ni émettre
un code, ni lister, ni révoquer.

Hors de chez vous, Chimera ne fait tourner **aucun relais à lui**. Si vous voulez atteindre le
listener des appareils depuis l'extérieur, placez devant lui un tunnel que vous contrôlez —
[Tailscale](https://tailscale.com/) sur les deux appareils, ou un [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
vers `http://127.0.0.1:<port>` — et décidez-le en connaissance de cause : il prolonge la même porte
jusqu'où le tunnel arrive. Ne liez jamais l'application elle-même à `0.0.0.0` pour cela.

## Le backend de l'application de bureau

Le backend de l'application écoute en loopback, et loopback n'est pas une personne : n'importe quel
processus de la machine atteint le port.

- **Un jeton sur chaque route.** L'application de bureau démarre toujours son backend avec un jeton
  bearer — un secret neuf à chaque lancement, ou le `CHIMERA_SERVER_TOKEN` que le propriétaire a déjà
  défini. Toute route `/api/…` répond alors `401` sans lui, `/api/health` comprise. Seuls les fichiers
  statiques de l'application et l'application invité (`/guest/api/…`, qui vérifie ses propres jetons
  de partage) restent ouverts ; le pont MCP (`/api/bridge/…`) garde son propre jeton. Le shell remet
  le jeton de lancement au backend dans l'environnement et à la fenêtre par le script
  d'initialisation de la webview — jamais sur une ligne de commande, dans une URL ou dans un fichier.
  Ce que le backend lance n'en hérite pas : l'environnement des enfants du sandbox et celui avec
  lequel tournent `gh` et `git` écartent toute variable au nom de secret (sauf `GH_TOKEN` et les
  autres identifiants propres à gh).
- **Approuver sans le code exige l'écran.** Un jeton dit qui peut appeler l'API, pas que le
  propriétaire regarde. Un oui sans code (`POST /api/approvals/{id}`) ne passe que si la requête porte
  aussi l'identifiant d'écran dans `X-Chimera-Screen`, à côté du jeton bearer. Il est tiré à chaque
  démarrage et gardé en mémoire : jamais dans un fichier, un enregistrement, un log, le `.env` ou la
  réponse d'une route, jamais égal au jeton bearer, et retiré de l'environnement dès sa lecture (le
  shell le passe en `CHIMERA_SCREEN_TOKEN`). Tout jeton lu sur le disque — `service.token`, un
  `CHIMERA_SERVER_TOKEN` dans le `.env` — a toujours besoin du code par question pour un oui ; un
  refus n'a besoin d'aucun code. `chimera service` ne détient aucun identifiant d'écran, donc là tout
  oui demande le code. `chimera app` dans un navigateur le place dans la page qu'il sert à une requête
  loopback (`<meta name="chimera-screen">`), exactement là et quand cette page porte déjà le jeton
  bearer — la même exposition, par décision du propriétaire du 16/10/2026.
- **Le service en arrière-plan.** `chimera service start` exécute le même backend sans fenêtre. Il
  publie `~/.chimera/service-<channel>.json` (0600, écrit de façon atomique ; `CHIMERA_SERVICE_DIR`
  le déplace) avec le jeton persistant du propriétaire (`<home>/service.token`, ou un
  `CHIMERA_SERVER_TOKEN` choisi par le propriétaire), et un client ne fait confiance à ce fichier
  qu'après que `GET /api/info` a répondu avec les mêmes pid, version, id et dossier de données. Ce
  jeton ouvre l'API entière, il reste donc séparé de celui du pont : rien côté MCP ne lit les fichiers
  du service, et le pont les refuse en tant que fichiers. L'enregistrement de lancement de
  l'application installée, `~/.chimera/desktop-install.json`, ne contient que quatre variables de
  chemin — `CHIMERA_HOME`, `CHIMERA_WORKSPACE`, `CHIMERA_SHELL_PREFS`, `CHIMERA_SHELL_STATE` —, son
  écrivain refuse tout autre nom, et `start` le valide avant d'exécuter quoi que ce soit à partir de
  lui.

### Contrôle total par le pont

Le pont du bureau (`chimera mcp desktop`) a trois interrupteurs, chacun sans effet sans le précédent :
**Autoriser Claude à utiliser cette app**, **Contrôle total** et, depuis la décision du propriétaire
du 2026-10-18, **Contrôle total par le pont**, dans Réglages > Sécurité.

- **Seul l'écran l'active.** `CHIMERA_BRIDGE_FULL_CONTROL` n'est pas dans la liste des réglages
  modifiables : `PATCH /api/config` le refuse à tous et le `settings.edit` du pont le refuse par son nom.
  Sa seule route, `POST /api/security/bridge-full-control`, refuse toute requête relayée par le pont et
  ne l'active qu'avec l'identifiant d'écran ; le jeton du propriétaire seul est `service.token`, lisible
  sur le disque. L'écran demande confirmation. Le désactiver ne demande pas l'écran : cela ne fait que
  restreindre.
- **Ce qu'il ajoute.** Les décisions du propriétaire (autoriser des commandes dans un dossier,
  enregistrer un agent avec ses outils, règles d'approbation mémorisées, tout refuser, mémoriser un
  projet, répondre à une suggestion de réglage, plugins et marketplaces, nettoyage des worktrees), les
  routes qui exigent le `confirm: true` de la personne (restaurer, supprimer et exporter un design, les
  nettoyages du stockage, redémarrer et réinitialiser), les réglages du propriétaire — posture,
  protections, approbations, plafonds, modèles et planification, écrits au lieu d'être suggérés — et
  ouvrir un écran de l'app (`ui.navigate`, une adresse vérifiée contre les écrans de l'app, jamais une
  URL).
- **Ce qu'il n'ajoute jamais.** Les identifiants : aucune clé, jeton, mot de passe ni secret OAuth n'est
  lu ou écrit ; le Runner, ajouter un serveur MCP ou s'y connecter restent au propriétaire, et le coffre
  de clés, la lecture du `.env` par l'agent et l'URL de base du fournisseur ne sont pas modifiables. Et
  qui peut atteindre l'app : l'association d'appareils, le partage, la porte LAN, le jeton du serveur,
  les bots et leurs listes d'accès.
- **Le noyau décide d'abord.** Une approbation répond à un REVIEW ; un BLOCK du noyau n'atteint jamais
  l'approbateur. Les contrôles par exécution restent : une exécution lancée par le pont utilise les
  modèles et la posture configurés.
- **Visible et enregistré.** Tant qu'il est actif, chaque écran affiche une barre avec **Désactiver** en
  un clic, et chaque appel fait sous cette autorité écrit une entrée d'audit (`via: "bridge"`,
  `scope: "full_control"`, la route, le statut et les ids — jamais un corps ni une valeur), listée dans
  l'onglet Sécurité.
- **Limite honnête.** L'interrupteur vit dans `.env`, comme les deux autres : un processus qui peut
  écrire ce fichier l'active sans l'écran. Les outils de l'agent et le pont refusent le fichier ; le
  reste de ce qui tourne sous le propriétaire, non.

## Ce que le propriétaire décide, et ce qu'un dépôt ne peut pas

- **Politique de déploiement, uniquement restrictive.** `<CHIMERA_HOME>/policy.json` (ou le fichier
  que nomme `CHIMERA_POLICY_FILE`) contient des règles ordonnées `provider.use` / `integration.use` /
  `tool.use` ; la dernière qui correspond décide. Un `allow` n'y accorde jamais rien : il annule
  seulement un deny antérieur du même fichier, il ne peut donc rouvrir ni `CHIMERA_TOOL_DENYLIST`, ni
  le plancher de portée, ni un BLOCK du noyau. Un fichier invalide n'est jamais appliqué en partie :
  `serve`, `chat`, `assist`, `tui` et `acp` refusent de démarrer, `doctor` échoue, et le bureau démarre
  mais refuse tout outil jusqu'à sa correction. L'agent ne peut pas le réécrire — les outils
  d'écriture refusent le fichier où qu'il soit, et le pont ne peut pas le rediriger.
- **« Toujours dans ce projet ».** Sur une carte d'approbation, le propriétaire peut mémoriser un oui
  pour un projet : l'outil, ou, pour une commande shell, ses deux premiers mots. Il répond ensuite aux
  questions qui correspondent, **y compris après que le run a lu du contenu non fiable** — décision du
  propriétaire du 15/10/2026, en sachant qu'une page hostile pourrait orienter une commande à
  l'intérieur de ce préfixe. Il ne fait que sauter la question : un BLOCK du noyau et le plancher de
  portée l'emportent toujours, et une commande qui enchaîne, passe par un pipe ou redirige (`;`, `&&`,
  `|`, `>`, `$(`…) ne correspond jamais. Seul le propriétaire en crée ou en révoque un ; le pont ne le
  peut pas, sauf si le propriétaire a activé le contrôle total par le pont (ci-dessus).
- **Références nommées.** Les dossiers que le propriétaire liste dans `<CHIMERA_HOME>/references.json`
  sont accessibles en `@alias/path` par les seuls outils de lecture (`read_file`, `list_dir`, `grep`,
  `glob`) ; tout outil d'écriture refuse un alias. Une racine du système de fichiers, le home de
  l'utilisateur ou le dossier de données sont refusés comme référence. Seules les surfaces
  accompagnées les reçoivent — `chat`, `assist`, `tui`, un `solve` accompagné, le tour Code du bureau
  (jamais celui d'un invité) —, si bien que les bots, le cron et `scenarios` n'en ont aucune.
- **Agents en Markdown.** `.chimera/agents/*.md` dans un dépôt est le texte de quelqu'un d'autre, il
  ne peut donc que restreindre : ses outils sont intersectés avec les outils gouvernés de la session,
  ses étapes plafonnées par celles de la session, les clés inconnues refusées, son `model` ignoré, et
  son corps est clôturé comme donnée non fiable. Seuls les fichiers du propriétaire dans
  `<CHIMERA_HOME>/agents` peuvent fixer un modèle du catalogue.
- **Chats liés à des conversations.** `/link` dans Discord ou Telegram fait qu'un chat poursuit une
  conversation Code, et seul le propriétaire inscrit dans l'allowlist de ce bot peut en lier un — sans
  allowlist, personne. Le texte d'un inconnu dans un salon lié n'atteint jamais la conversation, et
  les médias sont refusés plutôt que transmis. Le tour s'exécute sur le plancher du bot, pas sur celui
  du bureau : seulement les serveurs MCP de `CHIMERA_BOT_MCP_SERVERS`, aucun connecteur OpenAPI, ni
  autorisation shell ni host exec.
- **Les connecteurs du propriétaire restent loin des portes qu'un inconnu peut ouvrir.** Un
  connecteur OpenAPI appelle l'extérieur avec la clé du propriétaire, il répond donc à quiconque
  pilote le run. Il ne se charge pas sur une surface de messagerie tant qu'un bot configuré n'a pas
  d'allowlist, jamais sur un job d'issue GitHub (n'importe qui écrit l'issue ; seul le dépôt est
  vérifié), et sur `/v1/chat/completions` seulement tant que `CHIMERA_SERVER_TOKEN` est défini. Un
  module, `chimera/governance/surfaces.py`, décide quel type de surface est un nom, par sa famille
  (`platform:discord` est un bot) ; un test échoue si un module réécrit sa propre liste.

## Limites honnêtes

Ceci mesure si l'action nuisible d'un agent *déjà injecté* est stoppée — pas si le modèle peut
être injecté en premier lieu. Le raisonnement libre sur du texte non fiable, et l'exfiltration à
travers des outils légitimement nécessaires, restent des problèmes ouverts (suivis dans
l'[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

La politique complète, toujours à jour, se trouve dans
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), y compris
comment signaler une vulnérabilité.

## Limites du contournement des permissions de session

Le contournement facultatif des permissions de session Code est authentifié par une clé HMAC générée en mémoire du processus ; cette clé n’est jamais écrite sur disque ni héritée par les processus enfants. Les indicateurs `bypass_permissions` persistés et les attestations `via_screen` mises en file sans HMAC valide sont ignorés. Le choix de permission ne dure donc que jusqu’au redémarrage du processus de l’application/service ; le contournement est alors désactivé par défaut. Un shell peut toujours modifier le JSON, mais pas falsifier l’attestation. Les arrêts lexicaux refusent les push Git détectés, la publication de paquets/containers et certaines mutations de l’API GitHub, les jetons reconnus dans les fichiers de secrets (dont `.npmrc`, `.netrc`, `.pypirc`, `.git-credentials` et les identifiants AWS), ainsi que certaines options HTTP d’envoi/de données ou destinations distantes `rsync`/`scp`. Ce sont des heuristiques, pas une frontière de sécurité : la détection n’est pas garantie pour les noms développés par glob comme `cat .e*`, les chaînes assemblées dynamiquement comme `python -c "open('.'+'env')"`, les encodages alternatifs, les wrappers ou les commandes hors de la syntaxe reconnue. Une page malveillante peut encore les contourner. Utilisez le bac à sable isolé pour tout travail non fiable.
