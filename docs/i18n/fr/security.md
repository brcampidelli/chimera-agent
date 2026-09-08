---
source_sha256: 0f6fea8c584991f0722cb5e5454502c825585f4066f672324c4ab3011ca109dd
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

## Les couches

- **Noyau de gouvernance** — chaque appel d'outil gouverné est allow / warn / review / block.
  Un premier filtre bon marché des signatures shell dangereuses, pas la frontière.
- **Sandbox** — un conteneur éphémère, réseau désactivé (`CHIMERA_SANDBOX=docker`), durcissable
  avec gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`).
  La **commande verify** s'exécute dans le même sandbox que le shell de l'agent, et quand ce
  sandbox n'est pas isolé, une commande que vous n'avez pas tapée — déduite du dépôt, lue depuis
  une tâche cron, une carte ou un workflow — passe par la même confirmation `CHIMERA_HOST_EXEC` ;
  refusée, elle s'abstient au lieu de s'exécuter (`CHIMERA_VERIFY_NETWORK=1` donne le réseau à un
  vérificateur docker ; sur les sandboxes noyau il ne le peut pas, donc un vérificateur qui a
  besoin du réseau tourne sur l'hôte, et uniquement si c'est vous qui l'avez tapé).
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

## Mesuré, pas affirmé

```bash
chimera redteam
```

fait passer un corpus d'injection dans la pile. Sur le corpus intégré, la couche de contamination
réduit le **taux de réussite des attaques de 100 % à ~14 %** — et le rapport *nomme* ce qui passe
encore (exfiltration via un outil autorisé) plutôt que de prétendre à 100 %.

La même commande imprime le **coût**, ce que la première version de cette page ne faisait pas :
sans personne à qui demander, la restriction refuse **100 % du travail légitime ayant d'abord lu
quoi que ce soit d'externe** — corriger le fichier que l'issue nomme, appliquer la mise à jour que
la documentation décrit — et la gate enregistrée (sur-blocage ≤ 5 %) échoue. Ce chiffre n'est pas un
problème de réglage ; la gate était vide. Le mode d'approbation par défaut est `ask`, et sur le
desktop il demande désormais vraiment : un appel d'outil restreint devient une question à
l'écran, accompagnée de la raison inscrite au registre, à laquelle on répond par un bouton ou
`chimera approve`, et que le silence refuse après `CHIMERA_APPROVAL_WAIT` secondes. Avec la
personne qui approuve le travail qu'elle a demandé, le sur-blocage est de 0 % et le taux de blocage
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

- **La configuration livrée échoue à sa propre gate — sur le coût.** Elle marque 100 % du poison et
  détruit 25 % de la mémoire honnête pour y arriver. Les victimes sont nommées : un document de
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

## Limites honnêtes

Ceci mesure si l'action nuisible d'un agent *déjà injecté* est stoppée — pas si le modèle peut
être injecté en premier lieu. Le raisonnement libre sur du texte non fiable, et l'exfiltration à
travers des outils légitimement nécessaires, restent des problèmes ouverts (suivis dans
l'[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

La politique complète, toujours à jour, se trouve dans
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), y compris
comment signaler une vulnérabilité.
