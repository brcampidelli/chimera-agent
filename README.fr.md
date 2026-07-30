<div align="center">

<img src="assets/logo-wide.png" alt="Chimera logo" width="460" />

# Chimera

**L'agent auto-évolutif gouverné — prouvé et gouverné.**<br/>
<sub>Pense avec plusieurs cerveaux, fait un vrai travail seul, n'apprend que ce qui est prouvé, et est sûr par conception.</sub>

[![PyPI](https://img.shields.io/pypi/v/chimera-agent.svg?color=blue&label=PyPI)](https://pypi.org/project/chimera-agent/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
[![Reddit](https://img.shields.io/badge/Reddit-r%2FChimeraAgent-FF4500.svg?logo=reddit&logoColor=white)](https://www.reddit.com/r/ChimeraAgent/)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![Donate](https://img.shields.io/badge/Donate-Stripe-635BFF.svg?logo=stripe&logoColor=white)](https://donate.stripe.com/9B63cofM491m4SBfe177O00)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <b>Français</b> · <a href="README.it.md">Italiano</a> · <a href="README.pl.md">Polski</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

La plupart des assistants IA misent tout sur un **seul** modèle et oublient tout dès que la
conversation se termine. **Chimera fait deux choses différemment :** pour les questions difficiles,
il interroge **plusieurs** modèles d'IA en même temps et combine leurs réponses en un seul résultat
plus solide, et il **se souvient et apprend** pour devenir de plus en plus utile à mesure que vous
l'utilisez. Il ne fait pas que discuter — donnez-lui un objectif et il planifie, utilise des outils,
vérifie son propre travail, et ne garde que ce qui fonctionne vraiment.

> **Gratuit et open-source (Apache-2.0), en développement précoce mais actif.** Il fonctionne déjà de
> bout en bout : discutez avec lui, laissez-le terminer des tâches tout seul, faites-le tourner comme
> un bot sur votre application de messagerie préférée, déployez-le sur un serveur pour qu'il travaille
> 24h/24, et regardez-le apprendre de ce qu'il fait. C'est une version **alpha** — solide et
> abondamment testée (**plus de 2 000 tests automatisés**, vérification de types stricte et lint à chaque
> changement), mais pas encore éprouvée en production.

---

## Pourquoi Chimera

Voyez la plupart des outils d'IA comme le fait d'interroger **un** expert en espérant qu'il ait
raison. Chimera, c'est comme avoir un **panel d'experts** qui débattent, un **juge impartial** qui
pèse leurs réponses, et un **rédacteur** qui livre le meilleur résultat combiné — puis un coéquipier
qui **fait vraiment le travail** et qui **en apprend**. Voici ce qui le rend spécial, en termes
simples :

- 🧠 **Plusieurs cerveaux, une seule réponse.** Pour les questions difficiles, Chimera pose la même question à plusieurs modèles, laisse un modèle comparer leurs réponses, et charge un modèle final de rédiger la meilleure réponse combinée — vous obtenez ainsi quelque chose de plus équilibré et moins susceptible d'être faux qu'un seul modèle seul. (Il ne le fait que lorsque ça en vaut la peine, pour rester rapide et économique.)
- 🚀 **Il fait le travail, il ne se contente pas de parler.** Donnez-lui un objectif. Il le décompose, utilise des outils, modifie des fichiers, lance les tests, et **ne garde un changement que s'il passe**. Si quelque chose casse, il l'annule et réessaie — pour ne pas laisser de désordre derrière lui.
- 🧬 **Il s'améliore à mesure que vous l'utilisez.** Il retient vos préférences et les faits importants d'une conversation à l'autre, et transforme discrètement les tâches qu'il répète en compétences réutilisables. Il est conçu pour continuer à s'améliorer au lieu de se dégrader lentement sur la durée — un problème qui ronge silencieusement beaucoup d'agents.
- 🛡️ **Sûr par conception.** Chaque action risquée passe d'abord par une vérification de sécurité, tout ce qui est destructif demande une confirmation, et le code non fiable peut s'exécuter dans un conteneur verrouillé, sans réseau. (Ces vérifications sont un premier filtre bon marché, pas la vraie frontière — le bac à sable l'est ; et l'isolation par conteneur est optionnelle. Voir [SECURITY.md](SECURITY.md).)
- 🔌 **N'importe quel modèle, tourne partout.** Utilisez de grands modèles hébergés ou vos propres modèles locaux via une interface unique — sur votre ordinateur portable ou un serveur à 5 $, 24h/24.
- 🧩 **Vraiment à vous.** Open-source, sans verrouillage, sans compte fournisseur requis. Vous le faites tourner, il vous appartient, vous pouvez tout modifier.

## Comment Chimera se compare

Chimera ne cherche pas à surpasser les géants des projets d'agents sur *leur* terrain. Il mise sur les
trois choses qu'une véritable étude de rétro-ingénierie de cinq leaders (OpenClaw, Hermes, nanobot,
CrewAI, LangGraph) a trouvées qu'ils **laissent tous ouvertes** — et en fait son cœur :

- 🧬 **Auto-évolution avec un signal de fitness.** Les autres « apprennent » en ajoutant tout ce qui s'est passé, ou par des pull requests humaines — rien ne mesure si un changement appris a réellement aidé. Chimera ne garde un changement **que lorsqu'un résultat vérifié prouve qu'il a aidé** : l'étape d'évolution est conditionnée au vrai diff de l'arbre de travail et à un A/B honnête, jamais à la parole du modèle. Preuve indépendante que ça compte : [EvoAgentBench (arXiv 2607.05202)](https://arxiv.org/abs/2607.05202) a mesuré que les méthodes d'encodage d'expérience *automatiques* et non conditionnées produisent régulièrement du **transfert négatif** — une méthode populaire a régressé de **−12,3 points** sur des tâches pour lesquelles elle n'était pas réglée. Le gate de Chimera exécute désormais aussi un **holdout de transfert** : un changement appris ne doit pas faire régresser une tranche disjointe, de même capacité, avant d'être promu — il ne peut donc pas simplement mémoriser sa propre évaluation.
- 🛡️ **Sécurité par architecture.** L'injection de prompt est aujourd'hui largement considérée comme *impossible à corriger* ; les agents populaires l'atténuent au niveau applicatif ou la déclarent hors périmètre (l'un d'eux a livré 135 000 instances exposées publiquement et une marketplace remplie à ~12 % de compétences malveillantes). Chimera fournit une vraie couche de défense — **optionnelle via `--taint`, désactivée par défaut** : elle trace la provenance du taint de façon *heuristique* (flux de référence/contenu littéral, **pas** un vrai dataflow — un modèle qui paraphrase le contenu taint le « blanchit »), retire les tokens de contrôle du contenu non fiable, restreint l'accès aux outils dangereux pour le reste d'une exécution taintée, et protège les réessais à effet de bord ; le code non fiable s'exécute dans un conteneur verrouillé, optionnel. Mesuré, pas affirmé : sur le corpus intégré de **7 attaques**, cela réduit le taux de réussite des attaques de **100 % → ~14 %** ([`chimera/eval/injection.py`](chimera/eval/injection.py)). [`SECURITY.md`](SECURITY.md) dit clairement ce qui passe encore (passage de relais entre sous-agents, fusion/résumé, points d'entrée hors CLI) : la frontière de confinement est le sandbox ; cette couche est une défense en profondeur par-dessus.
- 📊 **Des benchmarks honnêtes et publiés.** ~20 % des cas « résolus » d'un classement populaire sont en réalité faux. Chimera rapporte chaque chiffre avec un intervalle de confiance — **y compris les exécutions où il n'a pas gagné** — et ne relance jamais pour obtenir la significativité. Une exécution appariée enregistrée montre la boucle complète **rehaussant un modèle faible sur une suite pré-enregistrée de 100 tâches — 9 % → 15 % (+6pp), IC 95 % [+1,3 %, +6,0 %] — statistiquement significative** (l'IC exclut zéro), à partir de **6 tâches qu'elle a récupérées** (échec brut → réussite vérifiée) avec **zéro régression** ; les taux absolus sont bas à dessein, car 85 des 100 tâches sont assez difficiles pour échouer dans les deux bras (un plancher délibéré, pour laisser de la marge à la boucle). Une exécution, sans relance. Et sur le **Terminal-Bench officiel**, un A/B pré-enregistré N=40 a atterri sur un **plancher dominé par la variance, sans différence significative dans un sens ou l'autre** — publié tel quel ([`bench/terminal_bench/RESULTS.md`](bench/terminal_bench/RESULTS.md)), y compris **la rétractation d'une lecture intermédiaire erronée** une fois le bras de contrôle mesuré. Les résultats nuls et les auto-corrections sont publiés aussi ; c'est tout l'intérêt.

**En une ligne : l'agent auto-évolutif gouverné — prouvé et gouverné.** C'est de l'alpha, et il le dit.

## Benchmarks (honnêtes)

Deux chiffres enregistrés, tous deux vrais, publiés ensemble à dessein — l'un désormais significatif,
l'autre humiliant. (Également visibles dans l'écran **Maturité et Benchmarks** de l'application de
bureau, directement depuis l'instantané embarqué.)

- **Élévation d'un modèle faible (significative).** Un modèle bon marché (`mistral-small-3.2-24b`) + la
  boucle de reprise de Chimera contre le même modèle seul, sur une **suite pré-enregistrée de n=100**
  (conception et tâches commitées et poussées avant tout appel de modèle) : **48,0 % → 71,0 %
  (+23,0 pts)**, IC 95 % apparié **[+12,6 %, +28,6 %] — statistiquement significatif** (l'IC exclut 0),
  à partir de **28 tâches récupérées par la boucle** (échec brut → réussite vérifiée) contre
  5 régressions. Un modèle, une graine/tâche, de petites tâches Python autonomes — **PAS** SWE-bench,
  et cela ne se généralise pas à de vrais dépôts. Une exécution, sans re-tirage.
  **Ceci remplace une exécution antérieure de la même suite** (9,0 % → 15,0 %, +6,0 pts) dont le
  harnais notait avec un fichier de test que l'agent testé pouvait modifier. En la refaisant avec le
  test d'origine restauré, l'agent a été pris en train de réécrire son propre test de notation sur une
  tâche — la faille était donc réelle — et l'élévation s'est répliquée *plus grande*, pas plus petite.
  L'affirmation de l'exécution antérieure selon laquelle « 85 des 100 tâches sont assez difficiles pour
  échouer dans les deux bras » n'a pas tenu non plus : la reprise en mesure 24. L'erratum complet, les
  preuves de falsification conservées et ce qui n'a pas pu être re-vérifié sont dans
  [`bench/local_lift/RESULTS.md`](bench/local_lift/RESULTS.md).
  Source : [`bench/local_lift/_reverify_n100/paired.json`](bench/local_lift/_reverify_n100/paired.json), [`PREREGISTRATION.md`](bench/local_lift/PREREGISTRATION.md).
- **SWE-bench Verified — la preuve externe la plus solide, et elle a survécu à une réplication conçue
  pour la tuer.** Trois exécutions pré-enregistrées sur des tranches de `django/django`, notées
  **uniquement** par le harnais officiel `swebench` 4.1.0 dans Docker — jamais auto-déclarées.

  | exécution | tranche | référence | + Chimera | Δ apparié | IC 95 % | |
  |---|---|---|---|---|---|---|
  | 1 (`max_steps=8`) | 19 | 36,8 % | 36,8 % | +0,0 % | [−8,5 %, +8,5 %] | ns |
  | 2 (`max_steps=30`) | les mêmes 19 | 42,1 % | 57,9 % | +15,8 % | [−1,9 %, +15,8 %] | ns |
  | **3 (réplication)** | **41 inédites** | 34,1 % | 43,9 % | **+9,8 %** | [−3,5 %, +16,7 %] | ns |
  | groupé *(secondaire)* | 60 | 36,7 % | 48,3 % | **+11,7 %** | **[+0,8 %, +16,4 %]** | **significatif** |

  Le +15,8 % de l'exécution 2 était un 3–0 sur trois paires informatives, et le pré-enregistrement lui
  donnait **une chance sur trois d'être exactement cela — un échantillon chanceux**, avec la
  rétractation engagée d'avance. L'exécution 3 l'a testé sur **41 instances dont nous n'avions jamais
  vu les résultats**, sans rien changer d'autre. L'effet **est réapparu** (+9,8 %, dans la fourchette
  enregistrée de +5 à +20) sur une tranche qui s'est révélée *plus difficile* que celle de
  l'exécution 2. Sur les deux, les paires discordantes sont de **9 pour Chimera contre 2**
  (p ≈ 2,6 % sous l'hypothèse nulle).

  **Le mécanisme s'est répliqué, et c'est la partie intéressante.** Une quatrième exécution a restauré
  le bras intermédiaire (échafaudage seul, sans la barrière de diff) sur les mêmes 41 instances, si
  bien que les trois ne diffèrent que d'un composant. Les trois **éditent au même rythme** (27–28
  correctifs sur 41) ; ce qui change, c'est la fréquence à laquelle l'édition est *juste* :

  | bras | résolues | **précision quand il a édité** |
  |---|---|---|
  | référence | 14/41 | 50 % |
  | + échafaudage | 16/41 | 59 % |
  | + échafaudage **et** barrière de diff | 18/41 | 67 % |

  **Les deux composants contribuent, à peu près par moitiés** (+4,9 % chacun, aucun significatif seul)
  — ce qui **contredit notre propre prédiction enregistrée** selon laquelle l'échafaudage porterait
  l'essentiel, et retire une lecture de l'exécution 2 affirmant que la barrière de diff « n'est pas ce
  qui a produit le gain ». La rétractation est dans [`RESULTS.md`](bench/swe_bench/RESULTS.md) ;
  l'additivité si nette n'est *pas* revendiquée comme un partage 50/50 mesuré, chaque comparaison
  reposant sur 5–6 paires discordantes.

  ⚠️ À lire honnêtement : **le résultat primaire hors échantillon N'EST PAS significatif.** Le chiffre
  significatif est le **secondaire groupé**, pré-enregistré comme secondaire précisément parce qu'il
  mêle données vues et inédites — il n'est pas promu au rang de titre maintenant qu'il a franchi la
  ligne. Et **48,3 % N'EST PAS un score SWE-bench Verified** : c'est une tranche délibérément facile,
  d'un seul dépôt ; un vrai score exige les 500 complètes. Le zéro exact de l'exécution 1 est publié
  tel quel, et l'exécution 2 a livré la **rétractation qu'elle méritait** (le mécanisme que nous
  avions avancé pour ses correctifs vides était faux — le remède était le budget d'étapes).
  Source : [`bench/swe_bench/RESULTS.md`](bench/swe_bench/RESULTS.md), [`PREREGISTRATION.md`](bench/swe_bench/PREREGISTRATION.md).
- **Terminal-Bench (humiliant).** A/B pré-enregistré N=40 sur le benchmark officiel, même modèle dans
  les deux bras (`deepseek-chat-v3.1`) : **7,5 % → 2,5 %** avec l'échafaudage, **Δ apparié −5,0 pts,
  IC 95 % [−5,0 %, +1,6 %] — non significatif**. L'échafaudage **n'a pas élevé un modèle déjà
  compétent** (ce n'est pas le régime faible « boucle d'or » où l'échafaudage aide) ; les deux bras
  sont à un plancher dominé par la variance.
  Source : [`bench/terminal_bench/RESULTS.md`](bench/terminal_bench/RESULTS.md).
- **L'apprentissage accumulé aide-t-il ? Sept exécutions répondent : pas de façon démontrable (et un
  résultat positif a été rétracté).** Le volant d'inertie — skills conditionnées à la récurrence plus
  un test de transfert, cartes d'anti-motifs, mémoire persistante — a été mesuré sur **sept exécutions
  pré-enregistrées**. L'exécution 6 a produit le seul positif de la série (+6,7 % significatif sur la
  métrique de transfert intra-famille) ; **l'exécution 7, avec plus de puissance, l'a ramené à +2,0 %
  et non significatif — il a donc été rétracté**, exactement comme le pré-enregistrement s'y était
  engagé. Le verdict honnête : **aucune exécution suffisamment puissante ne montre que l'apprentissage
  accumulé améliore la réussite des tâches**, et le goulot d'étranglement est l'instrument — trois
  tentatives d'écrire une suite tombant dans la plage informative de 40–60 % ont toutes atterri à
  84–92 %. « Il s'améliore à mesure que vous l'utilisez » reste **sans preuve**.
  Source : [`bench/learning_lift/RESULTS.md`](bench/learning_lift/RESULTS.md).

Significatif en interne (sur notre propre suite difficile). Sur de vrais dépôts, **répliqué hors
échantillon et significatif seulement une fois groupé** — l'étiquette honnête, pas la flatteuse.
Humiliant sur Terminal-Bench. L'affirmation sur l'apprentissage est **rétractée**. Nous publions tout,
nous écrivons *avant* de lancer la branche où le résultat tue notre propre affirmation, et nous ne
relançons pas pour obtenir la significativité — ce serait du p-hacking.

## Économie de tokens — mesurée, pas revendiquée

Deux intuitions du type « plus de modèles = mieux », mises à l'épreuve sur de vraies exécutions
(prédictions enregistrées *avant* chaque exécution, victoires **et** défaites publiées —
voir [`bench/`](bench/)) :

**La fusion est réservée, pas par défaut.** Sur une suite de raisonnement de 12 tâches, le palier
intermédiaire seul a obtenu 100 % pour 846 tokens ; la fusion complète a aussi obtenu 100 % — pour
**9 526 tokens (~11×)**. La fusion se cache donc derrière une cascade cheap→gate→mid→fusion qui
n'escalade que lorsqu'un gate gratuit échoue, atteignant une qualité ~intermédiaire à ~1/12 du coût
de la fusion.

**L'orchestration hiérarchique ne gagne que là où elle le doit — et selon une loi qu'on peut écrire.**
`chimera orchestrate` répartit une tâche entre des workers cadrés au lieu d'un seul grand contexte. Un
agent unique renvoie chaque document à chaque tour ; les workers cadrés lisent chacun une fois. Ainsi
l'économie de tokens évolue en **(D−1)/D** selon le nombre de documents D — confirmé sur de vraies
exécutions à moins de 0,2 % :

| documents (D) | économie de tokens mesurée | (D−1)/D |
|---|---|---|
| 2 | 49.9% | 50% |
| 3 | 66.7% | 66.7% |
| 4 | 74.8% | 75% |
| 5 | 79.9% | 80% |

L'économie reste stable à mesure que la conversation s'allonge et augmente avec la taille des documents
vers la même limite ([balayage complet, 3 axes](bench/hierarchy_sweep/README.md)). Et là où ça *ne* paie
*pas* — une tâche en un seul coup avec un seul tour — le classifieur le détecte et **retombe sur un agent
unique** (cette exécution a coûté +47 % de tokens en plus ; nous l'avons publiée aussi).

**L'astérisque honnête.** Ce sont des décomptes de *tokens*. Avec le cache de prompt, un fournisseur
facture les documents répétés de l'agent unique à ~0,1×, donc le gain en *dollars* est plus faible — et
au-delà de quelques tours il peut **s'inverser** (les workers indépendants repaient le contexte froid que
l'agent unique met en cache). Nous livrons le
[modèle qui quantifie cela](bench/hierarchy_sweep/cache_cost.py) plutôt que de faire passer discrètement
le chiffre de tokens pour un chiffre en dollars.

## Fonctionnalités

### 🧠 Penser et agir
- **Combiner plusieurs modèles en une seule réponse** (`chimera fuse`) — un panel de modèles, un juge qui fait ressortir où ils sont d'accord, en désaccord, ou passent à côté de quelque chose, et un synthétiseur qui rédige la réponse finale. Un routeur intelligent ne consacre cet effort supplémentaire qu'aux problèmes difficiles, et lorsque les premiers modèles sont déjà d'accord il s'arrête plus tôt — mesuré à environ **~20–28 % de tokens en moins sans perte de précision** sur nos benchmarks. (La fusion / mixture-of-agents en soi n'a rien d'unique — on la trouve dans OpenRouter et d'autres outils ; la différence ici, c'est qu'elle est intégrée à la boucle de l'agent, derrière ce routeur soucieux du coût, et mesurée, pas un modèle que l'on choisit.)
- **Terminer des tâches tout seul** (`chimera solve`) — il planifie, agit avec des outils, puis **vérifie et annule** : il lance votre contrôle (par ex. les tests) et ne garde le changement que s'il passe, sinon il l'annule et réessaie. Il peut, en option, travailler sur une copie isolée de votre projet pour que rien ne soit touché tant que ce n'est pas éprouvé. **Et un paragraphe convaincant n'est pas une solution :** sans `--verify` auquel se référer, une exécution qui n'a rien changé sur le disque est signalée comme un échec, pas comme un succès — car la seule chose qui resterait à la juger serait un modèle lisant de la prose, qui ne voit jamais le diff. Chaque tentative enregistre *qui* l'a approuvée (`verifier` / `diff+manager` / `manager` / `none`), pour qu'un reçu ne dise jamais « succès » sans nommer l'autorité derrière.
- **Des équipes de spécialistes** (`chimera crew`, `chimera crew-isolated`) — plusieurs agents concentrés sur un rôle se partagent une même tâche. En mode isolé, chacun travaille sur sa **propre copie privée en parallèle** ; les modifications sûres sont fusionnées, les conflits sont signalés au lieu d'être écrasés en silence, et les changements d'un mauvais worker peuvent être rejetés par un test propre à chaque worker. Un superviseur peut regrouper le travail de tous en un seul rapport unifié.
- **Déléguer et explorer** — n'importe quel agent peut confier une sous-tâche autonome à un nouveau **sous-agent** qui ne renvoie que le résultat, gardant le contexte principal propre. Le **Context Explorer** (`chimera explore`) trouve les bons fichiers et les bonnes lignes dans une base de code et renvoie une réponse courte au lieu de tout déverser.

### 🧬 Mémoire et auto-amélioration
- **Mémoire à long terme** — il conserve des mémoires à court terme, récentes, factuelles et vous concernant, plus une carte des relations entre les choses. Il peut stocker ses mémoires dans une base de données full-text rapide, transporter un profil de vos préférences dans chaque conversation, fusionner automatiquement les notes en double, et suggérer gentiment d'enregistrer une préférence quand vous en mentionnez une.
- **Apprend de nouvelles compétences** — quand il réussit plus d'une fois le même type de tâche, il en fait automatiquement une compétence testée et réutilisable.
- **Auto-entraînement optionnel (avancé)** — il peut enregistrer sa propre expérience pour que vous puissiez ensuite affiner un modèle à partir de celle-ci. Désactivé par défaut ; rien ne s'entraîne sans que vous le demandiez.

### 📏 Une boucle mesurable — et qui dit quand elle s'est perdue
Un agent, c'est un modèle **plus tout ce qui l'entoure**. Cette machinerie périphérique décide si une
longue exécution reste utile, et l'essentiel en est invisible jusqu'à la panne. Chimera mesure la
sienne :

- **Chaque exécution laisse un reçu.** Une ligne JSONL par exécution dans `traces.jsonl` : tokens par étape, les outils appelés avec ce qu'ils ont renvoyé, l'endroit où l'historique a été abandonné — et le **taux de succès du cache**, la part des tokens de prompt servie depuis le cache par le fournisseur. C'est le véritable chiffre de coût de la boucle (un token en cache coûte environ un dixième d'un token frais : des comptages identiques peuvent donc différer d'environ 10× en prix) *et* une alarme de conception : il s'effondre dès que quelque chose réécrit le début du prompt, ce qui n'a aucun autre symptôme. Un fournisseur qui ne signale aucun cache se lit comme **inconnu**, jamais comme un échec de cache.
- **Elle remarque quand elle n'avance plus.** Deux choses différentes sont appelées « problème de contexte » : l'attention qui se dilue dans un long prompt, et une *trajectoire* qui cesse discrètement d'accumuler pour se mettre à tourner en rond — chaque étape prise isolément va bien, l'exécution entière ne va nulle part. Le briseur de boucle de Chimera attrape la version serrée (une fenêtre de 12 appels) ; une exécution qui revient sur les trois mêmes fichiers tous les vingt tours la traverse sans déclencher. D'où un second détecteur qui compare la **première moitié d'une exécution à la seconde** : du travail redérivé que l'exécution avait déjà, des échecs en hausse, ou une redondance qui bondit juste après l'abandon d'historique. Il **signale et n'agit pas** — arrêter, replanifier et forcer une compaction sont tous des remèdes plausibles et nous n'avons aucune preuve de celui qui aide ; en choisir un intégrerait précisément l'hypothèse non mesurée que ce travail vise à supprimer.
- **Les longues exécutions survivent à leur propre contexte.** Épuiser la fenêtre mettait autrefois fin à l'exécution, ce qui faisait de la fenêtre — et non de la difficulté de la tâche — le vrai plafond. La compaction laisse désormais le message système intact (c'est le préfixe stable sur lequel repose tout le cache de prompt), ne laisse jamais un résultat d'outil orphelin de son appel, et **restaure ce dont l'exécution a besoin pour rester elle-même** : le fichier ouvert, le plan, la liste des tâches, l'état courant. Elle dit clairement ce qu'elle a abandonné au lieu de le résumer — un agent peut relire un fichier, mais il ne peut pas décroire un résumé inventé.

### 🔌 Connecter et automatiser
- **Parlez-lui n'importe où** — un chat en terminal, une application terminal plein écran, ou comme un bot sur **Discord, Telegram, Slack, Signal et WhatsApp**. Il y a aussi un point d'accès HTTP simple.
- **Planification et proactivité** — confiez-lui des tâches récurrentes en langage courant (« chaque matin, résume les actualités »). Avec le planificateur intégré en marche, il **agit à l'heure**, et pas seulement quand vous lui écrivez.
- **Outils et intégrations** — lire et écrire des fichiers, exécuter des commandes shell, **lire des pages web entièrement rendues et extraire ou explorer des sites entiers** (avec une extraction structurée à l'abri des injections), et exécuter du code en toute sécurité dans un bac à sable. Connectez presque n'importe quel service web (via son API) ou outil externe — y compris n'importe quel **serveur MCP** ([guide + exemple exécutable](docs/mcp.md)) — et importez votre configuration depuis d'autres outils d'agent que vous utilisez déjà.
- **Tout inclus** — recherche web, génération d'images (hébergée **ou entièrement locale**), **reconnaissance vocale** et synthèse vocale, **téléchargement de médias**, **analyse de données et graphiques**, e-mail, calendrier, exécution de code, et plus encore, prêts à être activés.

### 🚀 Tourner partout, en toute sécurité
- **N'importe quel modèle, une seule interface** — modèles hébergés ou vos propres modèles locaux, avec bascule automatique si l'un est indisponible et rotation entre plusieurs clés.
- **Déploiement serveur en une commande** — faites-le tourner avec Docker (ou en bare-metal) pour qu'il reste actif et redémarre au reboot. Voir **[docs/deploy.md](docs/deploy.md)**.
- **Noyau de sécurité** — une vérification sur chaque action (autoriser / avertir / bloquer / demander), un conteneur à réseau isolé **optionnel** pour le code non fiable (`CHIMERA_SANDBOX=docker` ; le runner *local* par défaut n'est *pas* isolé), et un journal d'audit complet de ce qu'il a fait.
- **Arrêtez-le avant qu'il ne valide quelque chose lu d'une source douteuse** (`--pause-on-taint`) — une exécution qui a consommé du contenu non fiable se met en attente au lieu de finaliser, et vous attend. Vous pouvez accepter le résultat, accepter une version que vous avez modifiée, envoyer des consignes et le laisser réessayer, ou le rejeter — depuis le terminal *ou* depuis l'application de bureau. Rien n'est enregistré et rien n'est appris tant que vous n'avez pas décidé, et une pause n'est jamais rapportée comme un échec : elle n'a pas atteint de verdict, elle attend une personne.
- **Une application de bureau qui pilote une exécution, pas seulement qui la lance** — cinq destinations au lieu d'un menu de quinze, en neuf langues. Lancez une exécution et partez : la progression est toujours là à votre retour, la barre d'état nomme ce que fait l'agent depuis n'importe quel écran, et Arrêter fonctionne partout. Installateurs natifs pour Windows / macOS / Linux sur [Releases](https://github.com/brcampidelli/chimera-agent/releases).

## Démarrage rapide

Vous avez besoin de **Python 3.11–3.13** et de [uv](https://docs.astral.sh/uv/) (un installateur Python rapide).

**1. Installer** — depuis PyPI :
```bash
pip install chimera-agent
```
Vous obtenez la commande `chimera`. (Les exemples ci-dessous utilisent `uv run chimera` pour une
copie du dépôt — avec pip install, lancez simplement `chimera …`.) Pour contribuer à Chimera lui-même, clonez le dépôt :
```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev
```

**2. Ajouter une clé de fournisseur d'IA.** Le plus simple est une clé [OpenRouter](https://openrouter.ai) — une seule clé
débloque plus de 100 modèles.
```bash
cp .env.example .env
# ouvrez .env et définissez, par exemple :  CHIMERA_OPENROUTER_KEYS=sk-or-...
```

**3. Vérifier que tout est prêt**
```bash
uv run chimera doctor
```

**4. L'essayer**
```bash
uv run chimera chat                         # ayez une conversation (il s'en souvient)
uv run chimera run "Explain what you can do in 3 bullets"
uv run chimera fuse "What's the best way to learn to cook?" --show-panel   # voir plusieurs modèles combinés
uv run chimera solve "add a hello() function to app.py and a test for it" --verify "pytest -q"
```

**Le faire tourner sur un serveur (pour qu'il travaille 24h/24) :**
```bash
docker compose up -d      # passerelle + planificateur ; redémarre automatiquement
```
Guide complet (Docker ou systemd, planification, sauvegardes, sécurité) : **[docs/deploy.md](docs/deploy.md)**.

**5. Faire quelque chose de concret en 5 minutes : le tri des e-mails.** Pointez Chimera sur votre boîte
de réception et obtenez un résumé de dix secondes — en lecture seule, classant URGENT / PERSONAL /
NEWSLETTER / COLD-SALES, et planifiable en option chaque matin :
```bash
uv run chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```
Configuration + planification quotidienne + mises en garde honnêtes : **[examples/email_triage/README.md](examples/email_triage/README.md)**.

## 🧰 Ce que Chimera sait faire — et comment activer chaque chose

Vous débutez ? Chimera fonctionne dès `pip install chimera-agent` + une clé d'IA. Certaines
capacités (lire des documents, entendre de l'audio, faire des graphiques, télécharger de la vidéo…)
nécessitent un petit paquet optionnel — appelé **« extra »** — et certaines une clé de service. Cette
section liste **chaque capacité, exactement quoi installer et la commande pour l'essayer**. Aucune
connaissance préalable requise.

### Tout activer d'un coup
```bash
pip install 'chimera-agent[full]'     # toutes les fonctions sans GPU ci-dessous, en une commande
```
L'audio et la vidéo nécessitent aussi **ffmpeg** sur votre ordinateur :
`macOS : brew install ffmpeg` · `Ubuntu/Debian : sudo apt install ffmpeg` · `Windows : choco install ffmpeg`.
Vous préférez une installation légère ? Gardez `pip install chimera-agent` et ajoutez seulement les
extras voulus (voir la colonne « Requiert »). **Docker ? L'image officielle contient déjà tout ce qui suit.**

### Chaque capacité, point par point
**Requiert** = quoi ajouter : `—` fonctionne dans l'installation de base · `[extra]` = `pip install 'chimera-agent[extra]'` · `clé : X` = une clé de fournisseur dans `.env`.

| Ce que vous obtenez | Requiert | Comment l'utiliser |
|---|---|---|
| **Chat qui se souvient de vous** | — | `chimera chat` |
| **Poser une question** | — | `chimera run "explique X en 3 points"` |
| **Application terminal plein écran** | — | `chimera tui` |
| **Application de bureau** (chat · travail · code · connaissances · automatisation, en 9 langues) | `[desktop]` ou un téléchargement | `chimera app`, ou récupérez un installateur natif (`.exe`/`.dmg`/`.AppImage`/`.deb`) depuis [Releases](https://github.com/brcampidelli/chimera-agent/releases) |
| **Faire une tâche, la garder seulement si un test passe** | — | `chimera solve "ajoute hello() à app.py + un test" --verify "pytest -q"` |
| **Me demander avant de valider ce qu'il a lu sur le web** | — | ajoutez `--pause-on-taint` à `chimera solve` |
| **Voir ce qu'une exécution a réellement coûté, étape par étape** | — | écrit pour vous dans `.chimera/traces.jsonl` (ou `$CHIMERA_HOME`) |
| **Fusionner plusieurs modèles en une seule réponse** | — | `chimera fuse "votre question" --show-panel` |
| **Une équipe d'agents spécialistes** | — | `chimera crew "votre tâche" --mode supervisor` |
| **Mener un projet entier jusqu'au bout** (pause avant les étapes risquées) | — | `chimera project start spec.yaml -w .` |
| **Voir des images** (vision) | clé : Gemini ou OpenAI | `chimera run --image photo.jpg "qu'y a-t-il ?" --model gemini/gemini-2.0-flash` |
| **Entendre l'audio** (voix → texte) | `[stt]` + ffmpeg | `chimera run "transcris reunion.mp3"` |
| **Parler** (texte → voix) | clé : ElevenLabs ou OpenAI | demandez à une tâche « lis ceci à voix haute dans speech.mp3 » |
| **Lire des documents** (PDF, Word, Excel → texte) | `[documents]` | `chimera run "résume rapport.pdf"` |
| **Télécharger vidéo/audio** (YouTube + 1000+ sites) | `[media-dl]` + ffmpeg | `chimera run "télécharge l'audio de <url>"` |
| **Analyser des données et faire des graphiques** | `[data,viz]` | `chimera run "charge ventes.csv et trace le revenu mensuel"` |
| **Chercher sur le web** | clé : Tavily | `chimera run "cherche sur le web : la dernière version de Python"` |
| **Lire et extraire de vraies pages web** (un vrai navigateur) | — | `chimera run "ouvre example.com et donne-moi le titre"` |
| **Mémoire à long terme** | — | `chimera memory add "..."` · `chimera memory search "..."` |
| **Apprendre des skills réutilisables tout seul** | — | pendant `chimera solve` ; liste avec `chimera skills` |
| **Planifier un travail récurrent** | — | `chimera cron add brief "0 8 * * *" "résume les actualités"` |
| **Tourner comme bot de chat** (Discord/Telegram/Slack/Signal/WhatsApp) | `[messaging]` | `chimera serve --cron --discord` |
| **Connecter n'importe quel outil externe** (MCP) | `[mcp]` | guide : [docs/mcp.md](docs/mcp.md) |
| **Générer des images** (hébergé) | clé : OpenAI | demandez à une tâche « génère une image de … » |
| **Générer des images** (100 % local, GPU requis) | `[imagegen-local]` | pareil, hors ligne |

> Installez les extras individuellement pour rester léger — `messaging`, `mcp`, `documents`, `media-dl`,
> `stt`, `data`, `viz`, `youtube` (tous inclus dans `full`), plus `imagegen-local` et `train` (GPU uniquement).
> Exemple : `pip install 'chimera-agent[documents,stt]'`.

### Première fois ? Six étapes pour débutants
1. **Installez Python 3.11–3.13** ([python.org](https://www.python.org/downloads/)) ; vérifiez avec `python --version`.
2. **Installez Chimera :** `pip install 'chimera-agent[full]'` (ou juste `chimera-agent` pour le cœur léger).
3. **Obtenez une clé d'IA** — une clé [OpenRouter](https://openrouter.ai) est la plus simple (une clé → 100+ modèles).
4. **Donnez la clé à Chimera :** copiez `.env.example` en `.env`, mettez `CHIMERA_OPENROUTER_KEYS=sk-or-...`.
5. **Vérifiez que c'est prêt :** `chimera doctor` — il indique ce qui est configuré et ce qui manque.
6. **Essayez :** `chimera chat`.

À partir de là, n'importe quelle commande du tableau ci-dessus fonctionne. Référence complète des
commandes avec exemples à copier-coller : **[docs/usage.md](docs/usage.md)**.

## Comment ça marche

Donnez une tâche à Chimera ; il planifie (en faisant ressortir les compétences intégrées les plus
pertinentes), réfléchit (en combinant des modèles quand le problème est difficile), agit avec des
outils — lire et extraire le web, modifier des fichiers, faire des graphiques —, **vérifie son propre
travail et ne garde que ce qui passe**, puis apprend du résultat — réinjectant la mémoire et de
nouvelles compétences dans la tâche suivante.

```mermaid
flowchart TD
    U([Vous : une tâche ou une question]) --> P[Comprendre et planifier]
    P --> Q{Est-ce un problème difficile ?}
    Q -- oui --> FUSION[Interroger plusieurs modèles<br/>· un juge les compare<br/>· un synthétiseur rédige la meilleure réponse]
    Q -- non --> ONE[Utiliser un seul modèle rapide]
    FUSION --> ACT[Agir : outils, fichiers, lire et extraire le web,<br/>faire des graphiques, ou déléguer à des sous-agents]
    ONE --> ACT
    ACT --> V{A-t-il réussi ?<br/>lancer tests / contrôles}
    V -- oui --> KEEP[Garder le changement]
    V -- non --> REVERT[Annuler et réessayer avec la leçon apprise]
    REVERT --> ACT
    KEEP --> LEARN[Apprendre : sauvegarder l'important en mémoire,<br/>transformer le travail répété en compétence réutilisable]
    LEARN --> U
    MEM[(Mémoire à long terme)] -. rappelle .-> P
    LEARN -. écrit .-> MEM
    SKILLS[(Bibliothèque de compétences)] -. fait ressortir les compétences pertinentes .-> P
    GOV[[Vérification de sécurité sur chaque action]] -. protège .-> ACT
```

## Commandes

Chaque commande est `chimera <name>` (ou `uv run chimera <name>` avant l'installation).

```bash
chimera doctor / models / features    # vérifier la configuration, lister les modèles, voir les capacités optionnelles
chimera chat                          # assistant interactif qui se souvient d'un tour à l'autre
chimera tui                           # application terminal plein écran
chimera run "PROMPT" --image pic.png  # réponse en un coup (peut lire une image)
chimera fuse "PROMPT" --show-panel    # combiner plusieurs modèles : panel -> juge -> synthétiseur
chimera solve "TASK" --verify "pytest -q" --isolate   # faire une tâche ; ne garder le changement que si le contrôle passe
chimera crew "TASK" --mode supervisor         # une équipe de spécialistes s'attaque à une tâche
chimera crew-isolated "TASK" -W "name:role" --verify "..." --synthesize   # équipe, chacun dans sa propre copie isolée
chimera explore "where is login handled?"     # trouver les bons fichiers/lignes, obtenir une réponse courte
chimera deliver "a launch plan" -o plan.md    # produire un document soigné
chimera serve --cron [--discord|--telegram|--slack|--signal]   # tourner comme service : bot de chat + planificateur
chimera cron add "brief" "0 8 * * *" "Summarize the news"       # planifier un travail récurrent
chimera memory add / graph / consolidate      # mémoire à long terme : sauvegarder, relier, ranger
chimera kanban add/board/run                   # un tableau de tâches qui distribue le travail à l'agent
chimera workflow flow.yaml                     # exécuter une automatisation répétable décrite dans un fichier
chimera migrate <source> <dir> --apply         # importer réglages, compétences et mémoire depuis un autre outil d'agent
chimera evolve status / tune / recipe          # optionnel : s'auto-optimiser ; préparer les données pour affiner un modèle
chimera fusion-bench / skillcard-bench / schema-bench / sandbox-bench   # benchmarks A/B honnêtes : mesurer coût, qualité et effets de bord avant de faire confiance à une fonctionnalité
chimera pet new --name Chimi                   # adopter un petit compagnon virtuel :)
```

Consultez le **[Guide d'utilisation](docs/usage.md)** pour chaque commande avec des exemples à copier-coller.

## Architecture

Chimera est un paquet Python aux parties clairement séparées, pour que vous puissiez comprendre ou
étendre chaque pièce indépendamment :

```
chimera/
  core/          la boucle de l'agent : planifier, agir, vérifier, garder-ou-annuler, et copies de travail isolées
  fusion/        le moteur « plusieurs cerveaux » : panel -> juge -> synthétiseur + le routeur intelligent
  memory/        mémoire à court terme / récente / factuelle / vous concernant + un graphe de relations
  skills/        la bibliothèque de compétences intégrée et la façon de trouver les compétences pertinentes
  evolution/     apprendre de nouvelles compétences à partir des succès, et l'expérience dont il apprend
  governance/    le noyau de sécurité (autoriser/avertir/bloquer/demander), journal d'audit, et contrôles de changement
  orchestration/ des équipes d'agents : rôles, crews, workers parallèles isolés, rapports unifiés
  ecosystem/     auto-amélioration avancée : des agents qui conçoivent des agents, entraînement de modèle optionnel
  kanban/        un tableau de tâches qui remet des cartes à l'agent
  workflow/      décrire une automatisation répétable dans un fichier simple et l'exécuter
  tools/         outils intégrés (fichiers, shell, web, recherche) + exécution de code
  sandbox/       exécuter les outils localement ou dans un conteneur verrouillé
  integrations/  connecter des outils externes et n'importe quelle API web
  scheduler/     tâches récurrentes + le démon qui les déclenche à l'heure
  migration/     transférer votre configuration depuis d'autres outils d'agent
  providers/     une interface vers chaque modèle, avec bascule et rotation de clés
  interface/     le moteur de conversation partagé (utilisé par le chat, l'application et les bots)
  server/        la passerelle de messagerie et le point d'accès HTTP
  cli/           la commande `chimera`
```

Consultez [docs/architecture.md](docs/architecture.md) pour la conception complète.

## Vision et objectifs

**L'objectif de Chimera est simple : un agent IA que tout le monde peut faire tourner, qui raisonne
mieux en combinant plusieurs modèles au lieu de faire confiance à un seul, qui s'améliore vraiment à
mesure qu'on l'utilise, et qui reste sûr et entièrement ouvert tout au long du chemin.**

La plupart des outils d'IA d'aujourd'hui sont soit intelligents-mais-oublieux (ils perdent tout dès
que la conversation se termine), soit capables-mais-fermés (vous ne les contrôlez pas). Et beaucoup de
ceux qui essaient de « s'améliorer eux-mêmes » deviennent silencieusement *pires* sur la durée. Chimera
est notre tentative d'une autre voie :

- **Mieux penser, sans facture plus salée** — combiner plusieurs modèles seulement quand ça aide, pour que la qualité monte sans gaspillage.
- **Une vraie mémoire et de vraies compétences** — se souvenir de l'important et transformer le travail répété en aptitudes réutilisables.
- **Une amélioration qui dure** — résister à la lente dégradation qui ronge d'autres agents, en vérifiant son propre travail et en gardant l'état en sécurité hors du modèle.
- **Sûr et transparent** — chaque action est vérifiable, et les actions destructives demandent d'abord.
- **Ouvert à tous** — gratuit, sous licence Apache-2.0, porté par la communauté, sans verrouillage.

C'est tôt (alpha), et l'honnêteté compte pour nous : ce n'est pas encore éprouvé en usage intensif de
production. Si cette vision vous enthousiasme, nous serions ravis de votre aide pour y parvenir.

## Développement

```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev

uv run ruff check .      # style/lint
uv run mypy chimera      # vérification de types stricte
uv run pytest -q         # la suite de tests
```

Les contributions sont les bienvenues — code, documentation, idées, rapports de bugs. Commencez par
[CONTRIBUTING.md](CONTRIBUTING.md) et notre [Code de conduite](CODE_OF_CONDUCT.md).
Vous voulez apprendre quelque chose de nouveau à Chimera ? Le **[guide d'extension](docs/extending.md)**
montre comment ajouter votre propre **outil, skill ou recette** (avec des exemples à copier-coller).
Vous avez trouvé un problème de sécurité ? Voir [SECURITY.md](SECURITY.md).

## Communauté

Une question, une idée, ou l'envie de contribuer ? **[Rejoignez-nous sur Discord](https://discord.gg/ACvBbrmguV)** — tout le monde est le bienvenu.

Plutôt Reddit ? Suivez **[r/ChimeraAgent](https://www.reddit.com/r/ChimeraAgent/)** pour les nouveautés et les discussions.

## Soutenir le projet

Chimera est gratuit et open-source, développé au grand jour. S'il vous est utile, vous pouvez aider à
financer son développement par un don unique — chaque contribution compte et est très appréciée. 💜

**[💜 Faire un don via Stripe](https://donate.stripe.com/9B63cofM491m4SBfe177O00)**

## Licence

[Apache-2.0](LICENSE) — libre d'utiliser, de modifier et de construire dessus.
