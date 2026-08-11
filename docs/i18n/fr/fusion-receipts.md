---
source_sha256: 39b9206b1943aee5c2c508bd1f97b3c9bb931d3a4d75eb5dc24f861497cdfe04
---

# Fusion receipts — « fusion sélective avec justificatifs »

Le cœur de raisonnement de Chimera mélange un **panel** de modèles (panel → juge → synthétiseur).
La fusion achète de la qualité mais coûte plus de tokens, donc la question honnête n'est jamais
« la fusion est-elle bonne ? » mais « **est-ce que ça en valait la peine, ici ?** ». Les receipts
répondent à cela avec des chiffres, pas une affirmation.

Chaque run de fusion peut être chiffré dans un **receipt** : ce qu'a coûté chaque advisor (membre
du panel), le juge, et le synthétiseur — chacun au tarif de *son propre* modèle — plus si le mode
sélectif a court-circuité le panel. Persistez les receipts et vous obtenez une **courbe coût ×
qualité** publiable.

## Essayer

```bash
# Afficher le coût détaillé par advisor d'un run :
chimera fuse "Explain CAP theorem simply" --show-cost

# Ajouter le receipt de chaque run à un JSONL, puis résumer la courbe :
chimera fuse "..." --receipt runs.jsonl
chimera fuse "..." --receipt runs.jsonl --selective
chimera fusion-receipts runs.jsonl
```

`fusion-receipts` rapporte le **taux de fusion** (à quelle fréquence le panel complet a réellement
tourné vs. un court-circuit sélectif), le coût moyen/total sur les runs dont le prix était connu,
et — quand les receipts portent un signal de qualité succès/échec — le taux de réussite et le
**coût en dollars par réponse réussie**.

## Règles d'honnêteté (par construction)

- **Les tokens sont mesurés ; les dollars sont estimés.** Le nombre de tokens vient du fournisseur ;
  le montant en dollars est calculé au **prix catalogue** public approximatif, donc un receipt est
  un estimateur, pas une facture.
- **Modèle inconnu → coût inconnu, jamais zéro.** Si une étape utilise un modèle sans prix
  enregistré, le total du receipt est `None` (`unknown`), pour qu'un prix manquant ne puisse pas se
  faire passer pour « gratuit ». Les prix sont surchargeables dans le code
  (`chimera.fusion.set_price`).
- **Attribution par advisor.** Le coût du panel est décomposé *par modèle*
  (`receipt.advisor_costs`), pour que vous puissiez voir quel advisor a mérité sa place — la
  substance derrière la fusion sélective, pas un slogan.

## Pourquoi ça existe

Le domaine s'est orienté vers le routage/les cascades (dépenser plus seulement quand l'enjeu le
justifie), et s'est éloigné de la fusion permanente. Les receipts sont ce qui permet à Chimera de
fusionner **sélectivement et de prouver que ça a payé** — la courbe coût × qualité est la preuve,
publiée en incluant les runs où la fusion n'a *pas* aidé.
