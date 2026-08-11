---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
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
réduit le **taux de réussite des attaques de 100 % à ~14 %** — et le rapport *nomme* ce qui passe
encore (exfiltration via un outil autorisé) plutôt que de prétendre à 100 %.

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
