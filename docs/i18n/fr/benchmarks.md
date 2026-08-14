---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# Benchmarks — prouver le gain sur modèle faible

La thèse de Chimera est que la structure fait qu'un modèle **faible/bon marché** frappe
au-dessus de sa catégorie. La manière honnête de le montrer est un A/B contrôlé sur un
benchmark standard : fixer le sous-ensemble de tâches et le modèle, faire du scaffolding la
**seule** variable, et rapporter le delta avec un intervalle de confiance — pas un simple « ça
s'est amélioré ». (Des recherches indépendantes trouvent que le même modèle varie d'environ 7
points par le seul scaffolding, donc un score non qualifié ne dit rien de *votre* contribution.)

## L'expérience

**Benchmark :** [Terminal-Bench 2.0](https://www.tbench.ai/) — tâche Docker + instruction +
tests de vérification, notée pass/fail par ces tests, pilotée par le harness agent-agnostique
**Harbor**.

- **Bras A (référence) :** un modèle gratuit dans le scaffold neutre de Harbor — « modèle faible
  seul ».
- **Bras B (traitement) :** le **même** modèle, les **mêmes** IDs de tâches, piloté par Chimera.
- **Métrique :** pass@1. **Chiffre clé :** Δ = taux(B) − taux(A), avec un IC à 95 %.
- **Garde-fous d'honnêteté :** fixer le sous-ensemble d'IDs de tâches (le publier), exécuter
  ≥3 seeds, publier toutes les transcriptions, et n'ajouter une ligne modèle-frontière que comme
  *référence plafond* — jamais comme comparaison.

### Le résultat — et il nous a contredits

Cette page terminait la section en nommant le nombre qui *prouverait* la thèse : « modèle gratuit
seul = X %, modèle gratuit + Chimera = Y %, Y ≫ X ». L'expérience a depuis été menée, et Y est
ressorti **en dessous** de X. Sur un sous-ensemble pré-enregistré de N=40 avec le même modèle dans
les deux bras (`deepseek-chat-v3.1`) : **7,5 % → 2,5 %**, **Δ appariée −5,0 pp, IC 95 % [−5,0 %,
+1,6 %] — non significatif**. L'échafaudage n'a pas fait progresser un modèle déjà compétent ; les
deux bras se situent sur un plancher dominé par la variance. Rapport complet, y compris le
pré-enregistrement rédigé avant l'exécution :
[`bench/terminal_bench/RESULTS.md`](../../../bench/terminal_bench/RESULTS.md).

La phrase promettant `Y ≫ X` a survécu à l'exécution qui l'a réfutée, sur cette page et dans neuf
traductions. Elle est consignée ici plutôt que supprimée en silence, car un projet dont le seul
actif réel est la mesure honnête ne peut pas se permettre une page qui prédit le contraire de son
propre résultat.

## L'exécuter

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimera se branche comme agent de traitement via `chimera/eval/terminal_bench.py`
(`make_chimera_tb_agent(model)` construit un `BaseAgent` Harbor qui exécute `chimera solve` avec
les flags de scaffolding). Pointez Harbor vers un sous-ensemble fixé et un modèle gratuit pour
chaque bras ; voir la [documentation Harbor](https://www.tbench.ai/) pour l'invocation exacte de
`harbor run` et `--agent-import-path`.

## SWE-bench Verified (le second tableau de bord) — **exécuté quatre fois**

Terminal-Bench prouve la thèse sur des tâches CLI ; SWE-bench la prouve sur de vraies
corrections de bugs GitHub — étant donné un dépôt à un commit de base et une issue, l'agent doit
produire un patch qui fait passer les tests `FAIL_TO_PASS` de l'instance tout en gardant les
`PASS_TO_PASS` verts. « Verified » est le sous-ensemble validé par des humains.

### Résultats

Quatre runs pré-enregistrés sur des tranches `django/django` (strate de
difficulté la plus facile), `deepseek-chat-v3.1`, pass@1, notés **uniquement** par le harness
officiel `swebench` 4.1.0 dans Docker. Compte-rendu complet :
[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md).

| run | référence | + Chimera | Δ apparié | IC 95 % | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36,8 % (7/19) | 36,8 % (7/19) | +0,0 % | [−8,5 %, +8,5 %] | non significatif |
| 2 (`max_steps=30`) | 42,1 % (8/19) | **57,9 % (11/19)** | **+15,8 %** | [−1,9 %, +15,8 %] | non significatif |
| **3 (réplication)** | 34,1 % (14/41) | **43,9 % (18/41)** | **+9,8 %** | [−3,5 %, +16,7 %] | non significatif |
| **groupé (secondaire)** | 36,7 % (22/60) | 48,3 % (29/60) | **+11,7 %** | **[+0,8 %, +16,4 %]** | **significatif** |
| 4 (attribution) | 34,1 % | *échafaudage seul* 39,0 % | +4,9 % | [−7,6 %, +14,2 %] | non significatif |

Le run 1 est un **zéro exact** et est publié sans modification. Le run 2 a corrigé deux défauts
qui étaient les *nôtres* — le scaffold tournait sans son mécanisme le plus fort, et 8 étapes
d'appel d'outils ne suffisent pas pour naviguer dans un dépôt de 250 Mo — et est ressorti avec
**3 instances gagnées, 0 perdue**. La paire est le résultat : le scaffold ne vaut *rien* quand
l'agent est privé d'étapes et *trois instances* quand il ne l'est pas, et il gagne en éditant
**mieux** (69 % contre 57 % de précision quand il édite), pas en éditant plus.

> ⚠️ **Aucun de ces chiffres n'est un score SWE-bench Verified.** La tranche est délibérément facile et
> mono-dépôt, choisie pour qu'un A/B apparié ait de la marge pour mesurer ; un vrai score
> Verified nécessite les 500 complets. Et le delta n'est **pas significatif** — avec 8 paires
> échec-échec, n=19 ne laisse que trois paires informatives.

Le run 2 livre aussi une **rétractation** : le mécanisme que nous avions retracé pour les
patchs vides du run 1 était erroné (le correctif était le budget d'étapes, pas la porte de diff
que nous avions blâmée), corrigé aussi ostensiblement qu'il avait été affirmé.

Ce 3–0 sur trois paires informatives est exactement la forme que produit un échantillon chanceux, et
le pré-enregistrement lui donnait **une chance sur trois de n'être que cela**. Le run 3 l'a donc
répété sur **41 instances dont nous n'avions jamais vu les résultats**, sans rien changer d'autre.
L'effet **a réapparu** : +9,8 %, dans la fourchette enregistrée de +5 à +20, sur une tranche qui
s'est révélée *plus difficile* que celle du run 2 (référence 34,1 % contre 42,1 %). Le run 4 a
ensuite séparé l'échafaudage du diff-gate sur les mêmes 41 : **+4,9 % chacun**, et le mécanisme est
la précision, qui monte de 50 % à 59 % puis 67 % alors que le taux de patch ne bouge pas. Aucun run
isolé n'est significatif ; le groupé n=60 l'est — et il a été pré-enregistré comme **secondaire**
précisément parce qu'il mélange données vues et non vues.

### L'adaptateur

L'adaptateur (`chimera.eval.swe_bench`) est honnête sur sa frontière : les parties pures —
l'invocation `chimera solve` par instance (bras de traitement) et l'analyse du rapport
d'évaluation officiel — vivent ici et sont testées unitairement ; le jeu de données et le
harness d'évaluation Docker sont **opt-in et non fournis avec le paquet**, et le verdict
pass/fail vient des propres tests de SWE-bench, jamais auto-déclaré.

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

Les deux rapports sont projetés sur la liste d'instances partagée (un id manquant compte comme
non résolu), donc les deux bras sont toujours comparés sur des instances identiques — puis le
même verdict Newcombe-CI s'applique.

## Noter l'A/B (aucun benchmark nécessaire)

Une fois que chaque bras a produit un pass/fail par tâche, les statistiques tiennent en une
commande — cela ne nécessite **aucun extra**, donc le moteur de reporting honnête est toujours
disponible :

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

Chaque fichier est une liste JSON de booléens (ou `{task_id: bool}`) sur les **mêmes** IDs de
tâches. Sortie : le taux de réussite borné par Wilson de chaque bras, le delta, son IC 95 % de
Newcombe, et si la différence est **significative** (l'IC exclut zéro). Si elle ne l'est pas,
c'est rapporté sans détour — un sous-ensemble plus large / plus de seeds, ou la fonctionnalité
ne fait vraiment pas bouger le chiffre.

Ce même `bench-compare` est l'étalon de mesure pour chaque fonctionnalité ultérieure : chaque
ajout M14 doit montrer qu'il fait bouger Δ sur le sous-ensemble identique, ou il est retiré.

## Le piège honnête (à éviter)

- **Contamination** — le SWE-bench public a une fuite de solutions documentée ; préférez des
  jeux résistants à la contamination et rapportez la mise en garde.
- **Confusion de scaffold** — ne jamais rapporter un « nous avons obtenu X % » brut ; seul le
  delta de l'A/B isole la contribution de Chimera.
- **Mauvaise référence / cherry-picking** — comparez faible+Chimera au *même modèle faible
  seul*, sur les IDs de tâches *identiques*, avec seeds et logs complets. Un modèle frontière
  est un plafond, pas un rival.
