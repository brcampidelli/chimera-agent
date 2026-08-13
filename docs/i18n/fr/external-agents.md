---
source_sha256: 91c3e19b5cb75bc83cafd3408047b7f1df85f8cb28d2be4b66897ce7a2e6a1ba
---

# Agents externes (ACP)

Chimera peut confier un tour de code à un agent qu'il n'a pas écrit — Claude Code, Gemini CLI, ou
n'importe quel adaptateur parlant l'[Agent Client Protocol](https://agentclientprotocol.com). La
transcription, le vérificateur, la sauvegarde et l'annulation restent ceux de Chimera ; le travail
est celui d'un autre.

## Pourquoi

La thèse de Chimera n'a jamais été que sa boucle soit la seule bonne. C'est la gouvernance autour
d'une boucle : le registre de contamination, la région d'écriture, la copie avant le tour, le verdict
après, le reçu qui dit ce qui s'est réellement passé. Cela vaut pour n'importe quel exécutant.
Refuser de piloter un exécutant auquel vous faites déjà confiance reviendrait à s'entêter sur la
moitié la moins intéressante du produit.

## Ce qui est garanti, et ce qui ne l'est pas

Lisez cette partie avant l'installation : c'est elle qui décide si cette fonctionnalité vous convient.

Un agent ACP déclare les capacités du client qu'il utilisera, et Chimera propose `fs/read_text_file`
et `fs/write_text_file`. **Proposer n'est pas imposer.** Les agents qui valent la peine d'être
pilotés ont leurs propres outils de fichiers et de terminal : Claude Code écrit via le Claude Agent
SDK et n'a aucune obligation de nous demander d'abord.

Concrètement :

| | Boucle propre à Chimera | Agent externe |
|---|---|---|
| La région d'écriture refuse en dehors d'elle | Toujours | Seulement ce qui passe par nous |
| Le shell tourne dans le bac à sable configuré | Toujours | L'agent exécute à sa façon |
| Le registre de contamination arme la barrière | Toujours | Seulement les outils que nous médiatisons |
| Instantané du dossier avant le tour | Oui | **Oui** |
| Annulation du tour entier en un clic | Oui | **Oui** |
| Chaque permission accordée figure au reçu | — | **Oui** |

Les trois dernières lignes sont la vraie garantie, et c'est ce que promet la ligne de posture de
l'écran Code lorsqu'un agent externe est sélectionné. Elle cesse de dire « modifie dans `/projet`,
n'exécute aucune commande » — cette phrase décrit des outils que Chimera contrôle — et dit à la place
qu'une copie a été prise et que le tour peut être annulé. Un écran qui garderait la phrase la plus
forte ferait une promesse que le tour ne peut pas tenir.

Chimera **décline** aussi la capacité terminal d'ACP. Un terminal hébergé par nous serait une seconde
voie d'exécution à côté du bac à sable, sans aucune de ses règles.

## Installation

Rien à configurer pour les agents que Chimera connaît :

```bash
npm i -g @agentclientprotocol/claude-agent-acp   # Claude Code, nécessite Node 22+
npm i -g @google/gemini-cli                       # Gemini CLI (son mode ACP est expérimental en amont)
```

Vérifiez ensuite ce que cette machine peut réellement lancer :

```bash
chimera doctor
```

`external_agents` rapporte chacun avec `available: true/false` et, quand c'est faux, la ligne qui
règle le problème. La disponibilité est résolue sur la machine où tourne le sidecar — qui, pour une
version de bureau empaquetée, est une machine assemblée par la CI que personne n'a regardée. Autrement
dit : « ça devrait être là » n'est pas une preuve.

L'application de bureau affiche une rangée **Qui exécute** au-dessus du compositeur, listant ce que
`doctor` a trouvé. Quand rien d'exécutable n'est installé, la rangée n'apparaît pas ; `doctor` est
l'endroit pour « vous ne l'avez pas encore, voici comment ».

## Identifiants

Chaque processus enfant lancé par Chimera reçoit un environnement dépouillé des variables `API_KEY` /
`TOKEN` / `SECRET`, pour qu'une commande shell ne puisse pas afficher une clé de fournisseur. Un
agent ACP est un programme dont tout le travail en dépend, donc chaque agent déclare **par leur nom**
les variables dont il a besoin, et seules celles-ci sont remises :

- Claude Code : `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`
- Gemini CLI : `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`

Passer tout l'environnement serait plus simple et donnerait à chaque futur adaptateur toutes les clés
de la machine.

## Un adaptateur à vous

Codex et d'autres atteignent ACP via des adaptateurs tiers que ce projet n'a pas exécutés. Plutôt que
de lister une commande non vérifiée — ce qui transformerait « nous n'avons pas vérifié » en « pris en
charge » — pointez Chimera vers celui que vous avez :

```jsonc
// POST /api/code/turn
{
  "message": "répare le test qui échoue",
  "provider": "custom",
  "provider_command": "npx -y un-adaptateur-acp --flag"
}
```

La commande est découpée à la façon d'un shell et exécutée **sans** shell : un tube égaré devient un
argument et non une seconde commande. Sous Windows, un argument contenant de la syntaxe cmd.exe
(`& | < > ^ %`) atteignant un lanceur `.cmd` est refusé plutôt qu'échappé : les règles de guillemets
diffèrent d'un lanceur à l'autre, et une mauvaise supposition exécute votre machine au lieu d'un
programme dessus.

## Comment ça marche

- Un processus enfant par **conversation**, pas par tour. Un `session/prompt` est un message dans un
  contexte que l'agent conserve ; un processus neuf à chaque fois ferait de chaque tour le tour un.
- Quatre vivants au maximum, et un laissé une heure sans usage est fermé. Chacun est un processus qui
  tient une connexion vers le modèle.
- Le processus naît dans son propre groupe et est tué en arbre — un agent de code est un lanceur, et
  ne tuer que le processus que nous tenons laisserait les ouvriers en cours et le dossier verrouillé.
  Un reaper `atexit` couvre le cas où l'application est quittée en plein tour.
- Les notifications `session/update` de l'agent sont traduites vers les mêmes événements que la
  boucle native, donc l'écran n'a pas besoin d'une seconde implémentation. Les fragments de
  raisonnement sont écartés plutôt que fondus dans la réponse ; un bloc `diff` devient le correctif
  unifié que la transcription affiche déjà.
- Les nombres que la boucle native possède et que celle-ci ne peut pas rapporter — `steps`,
  `context_peak_tokens` — arrivent en `null` et non en `0`. Zéro se lirait « il n'a rien fait ».

## Limites

- Les demandes de permission reçoivent `allow_once` et sont **inscrites au reçu**. Filtrer une demande
  que l'agent n'était pas obligé de faire est du théâtre ; la version honnête est d'accorder,
  d'inscrire, et de s'appuyer sur la sauvegarde — qui couvre aussi les écritures qui n'ont jamais
  demandé.
- La fusion, les rôles, la mémoire et la carte du dépôt appartiennent à la boucle propre de Chimera.
  Un tour externe rapporte `fused: false` et aucun usage mémoire, parce que rien de tout cela n'a eu
  lieu.
- Le mode ACP de Gemini est marqué expérimental en amont et son comportement peut changer d'une
  version à l'autre.
