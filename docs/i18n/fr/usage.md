---
source_sha256: f4c7b57b8bec8e9aa96ead432d65d90113b2b11c9e24c15f7f703c2c14520786
---

# Chimera — Guide d'utilisation

Chimera est un agent auto-évolutif, CLI-first, avec un cœur de raisonnement LLM-Fusion.
Ce guide couvre l'installation, la configuration, et chaque commande avec des exemples.

> Nouveau sur le projet ? Lisez d'abord la [vue d'ensemble de l'architecture](architecture.md).

---

## Installer

Chimera utilise [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

Chaque commande ci-dessous s'exécute comme `uv run chimera <command>` (ou simplement
`chimera …` une fois le virtualenv du projet sur votre PATH).

---

## Configurer

Chimera est agnostique au fournisseur via [LiteLLM](https://docs.litellm.ai/). Placez vos
clés et vos choix de modèle dans un `.env` local (il est ignoré par git — ne le committez
jamais) :

```dotenv
# At least one provider key. OpenRouter unlocks 100+ models behind one key.
OPENROUTER_API_KEY=sk-or-...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Tier-1/2 default model (single, cheap, must support tool-calling for Tier-2)
CHIMERA_DEFAULT_MODEL=openrouter/deepseek/deepseek-chat-v3.1

# LLM-Fusion: a diverse panel -> judge -> synthesizer
CHIMERA_FUSION_PANEL=openrouter/deepseek/deepseek-chat-v3.1,openrouter/openai/gpt-4o-mini,openrouter/meta-llama/llama-3.3-70b-instruct
CHIMERA_FUSION_JUDGE=openrouter/deepseek/deepseek-chat-v3.1
CHIMERA_FUSION_SYNTHESIZER=openrouter/openai/gpt-4o-mini
```

Autres réglages : `CHIMERA_HOME` (répertoire d'état, `.chimera` par défaut),
`CHIMERA_LOG_LEVEL` (`INFO` / `DEBUG`), `CHIMERA_CACHE` (`on`/`off`, désactivé par défaut —
met en cache les complétions identiques sans outil pour éviter les appels API répétés), et
`CHIMERA_AUTO_FUSE` (`on`/`off`, désactivé par défaut — fusionne automatiquement les tours
profonds ou **sensibles aux erreurs** dans `solve`/`crew` sans `--fuse` explicite ; le routeur
sensible au coût continue de garder les tours bon marché/avec outils sur un seul modèle). Le
routeur reconnaît les prompts à réponse exacte (arithmétique, comptage, opérations sur les
chiffres) dans les principales langues du projet (en/pt/es/de/fr/zh/ja), si bien qu'une étape
courte mais critique bénéficie de la protection de la fusion même quand elle est trop courte
pour déclencher la barrière de longueur.

**Fournisseurs, repli & auto-hébergement.** N'importe quel slug LiteLLM `provider/model`
fonctionne (`openai/…`, `anthropic/…`, `gemini/…`, `ollama/…`, `openrouter/…`, …). Pour un
serveur auto-hébergé / compatible OpenAI (Ollama, vLLM) définissez `CHIMERA_API_BASE` (par
ex. `http://127.0.0.1:11434` avec `CHIMERA_DEFAULT_MODEL=ollama/llama3`). Définissez
`CHIMERA_FALLBACK_MODELS` (séparés par des virgules) pour basculer vers un autre modèle si le
principal échoue. Dans `chat`/`tui`, `/model <slug>` change de modèle en cours de session.

**Pools d'identifiants.** Donnez à un fournisseur plusieurs clés avec
`CHIMERA_<PROVIDER>_KEYS` (par ex. `CHIMERA_OPENROUTER_KEYS=key1,key2,key3`). La passerelle
les fait tourner en round-robin entre les appels (répartition de la charge / des limites de
débit) et, au sein d'un même appel, bascule vers la clé suivante si l'une échoue. Un pool
remplace le `*_API_KEY` unique de ce fournisseur. *(Les connexions OAuth/par abonnement —
Copilot, Claude Max, etc. — ne sont pas encore câblées ; les clés API et tout endpoint
supporté par LiteLLM le sont.)*

Vérifiez que tout est bien câblé :

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**Fonctionnalités optionnelles.** La vision, le Mode Livrable (Deliverable Mode) et le Pet
sont intégrés. Le reste (recherche web, recherche X, génération d'images, TTS/voix, Spotify,
navigateur) sont des emplacements pré-configurés : renseignez l'identifiant correspondant
dans `.env` (ou installez la dépendance) et la capacité s'active. `chimera features` est la
checklist en direct. L'outil `web_search` (Tavily) s'auto-enregistre dès que
`TAVILY_API_KEY` est défini — et sert de modèle pour ajouter les autres (ou utilisez le
client MCP / l'importateur OpenAPI→tool).

> **Modèles gratuits vs payants.** Les modèles OpenRouter `:free` ne coûtent rien mais sont
> limités en débit en amont — correct pour un `run` rapide, capricieux pour des commandes à
> appels multiples comme `fuse`/`solve`. Pour un usage réel, un modèle payant bon marché
> (par ex. `deepseek/deepseek-chat-v3.1`, des fractions de centime par appel) est bien plus
> fiable.

---

## Commandes

### Statut — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — assistant interactif multi-tours (votre bras droit)

Un REPL interactif avec mémoire conversationnelle et usage d'outils — le pilote au
quotidien. Il rappelle la mémoire à long terme pertinente et enfile la conversation à
travers les tours.

```bash
uv run chimera chat                 # start chatting; /exit to quit, /reset to clear context
uv run chimera chat --fuse          # fuse deep-reasoning turns
uv run chimera chat --no-memory     # don't recall long-term memory
```

Le même cœur conversationnel alimente le TUI et (à venir) la passerelle de messagerie.

### `tui` — application plein écran dans le terminal

Une interface Textual plein écran au-dessus du même cœur conversationnel. Deux volets : un
**journal de conversation** qui affiche les réponses en Markdown (le code entre balises est
coloré syntaxiquement), avec les tokens du modèle qui **s'affichent en direct au fur et à
mesure** de leur arrivée ; et un **panneau d'activité** montrant ce que l'agent a fait ce
tour — les outils appelés, le nombre de tokens et le coût, et combien de faits mémorisés ont
été rappelés. Mêmes flags que `chat`.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
```

Commandes : `/model <slug>` · `/reset` (effacer le contexte) · `/clear` (effacer l'écran) ·
`/stream` (basculer les tokens en direct) · `/help` · `/exit`. Touches : `Ctrl+R`
réinitialiser · `Ctrl+L` effacer · `Ctrl+P` palette de commandes · `PgUp`/`PgDn` défiler ·
`Ctrl+C` quitter. Les commandes slash s'autocomplètent pendant la frappe.

Notes d'honnêteté : le streaming de tokens n'est disponible que sur le chemin mono-modèle —
sous `--fuse` (un tour panel→juge→synthétiseur) il n'y a pas de tokens incrémentaux, donc le
panneau affiche un statut « en cours de synthèse » plutôt qu'un faux curseur. Le coût affiche
« indisponible » quand le prix catalogue du modèle est inconnu (jamais deviné). Il n'y a
aucun indicateur verify/revert ici : verify-or-revert tourne dans `solve`/`project`, pas dans
`chat`. Si Textual n'est pas installé, `tui` retombe sur le simple REPL `chat`.

### `serve` — passerelle de messagerie (HTTP ou Discord)

Expose l'agent avec une conversation (et sa mémoire) **par chat**. Le cœur de routage est
agnostique au transport ; des adaptateurs se branchent dessus.

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Chaque `chat_id` conserve son propre contexte, donc différents utilisateurs/fils ne se
mélangent pas.

**Fonctionnement sans surveillance (webhooks).** Enregistrez une tâche qui se déclenche sur
un POST HTTP entrant, pour que Chimera tourne sans que personne ne tape quoi que ce soit — un
push GitHub, un événement Stripe, un ping de cron-as-a-service :

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

Le corps du POST est transmis à la tâche du job comme contexte, et chaque job enregistré
pour ce hook s'exécute. `GET /health` et `POST /chat` continuent de fonctionner en parallèle.

**Discord natif.** Faites tourner Chimera comme bot Discord — chaque canal est une session,
et l'agent peut aussi envoyer des messages via l'outil `send_message` :

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

Créez le bot sur <https://discord.com/developers>, activez l'intent **Message Content**, et
invitez-le sur votre serveur. Il répond dans tout canal qu'il peut voir (filtré pour ignorer
ses propres messages et ceux des autres bots). Le token est lu depuis l'environnement —
jamais codé en dur.

**Telegram natif.** Même pattern d'adaptateur, et il n'a besoin d'**aucune dépendance
supplémentaire** (l'API Telegram Bot est du simple HTTP) :

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**Slack natif.** Reçoit via Socket Mode (nécessite l'extra `messaging`) et envoie via la Web
API. Activez Socket Mode sur votre app Slack pour obtenir un jeton au niveau de l'app :

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp (envoi).** WhatsApp fonctionne en *push* (les messages arrivent sur un webhook
Meta que vous hébergez), donc contrairement aux autres il n'y a pas de connexion à ouvrir.
Définissez les identifiants Cloud API et l'agent peut **envoyer** des messages WhatsApp via
l'outil `send_message` dans n'importe quel mode `serve` :

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**WhatsApp bidirectionnel.** Pointez le webhook de votre app Meta vers
`https://<your-host>/whatsapp` et définissez `CHIMERA_WHATSAPP_VERIFY_TOKEN` (n'importe
quelle chaîne de votre choix, correspondant à la configuration de l'app). `chimera serve`
vérifie alors l'abonnement (`GET /whatsapp`) et route les messages entrants (`POST
/whatsapp`) à travers la passerelle, en répondant via la Cloud API. WhatsApp a quand même
besoin d'une URL publique pour le webhook — c'est la seule partie en dehors de Chimera.

**Signal natif (bidirectionnel).** Signal n'a pas d'API officielle, donc Chimera parle à un
pont [`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api) que vous
exécutez (Docker) et liez à votre numéro — du simple HTTP, aucune dépendance Python :

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — complétion en un coup, Tier 1

Un seul appel de modèle, aucun outil, aucune fusion. Le chemin le moins cher.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**Vision / collage d'image.** Attachez des images avec `--image` (un chemin ou une URL,
répétable) — nécessite un modèle capable de vision :

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Mode Livrable (produire un artefact)

Là où `run`/`chat` répondent de manière conversationnelle, `deliver` produit un document
complet et autonome (rapport, plan, spec, README...) et l'écrit dans un fichier.

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — la boucle brute d'appel d'outils ReAct

Pensée → Action (outil) → Observation, jusqu'à une réponse finale. Les outils sont limités
au workspace.

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion (le différenciateur)

Fait tourner un *panel* de modèles, un *juge* analyse leurs réponses (consensus /
contradictions / angles morts), et un *synthétiseur* rédige la réponse finale. Utilisez
`--show-panel` pour voir la trace complète.

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

La fusion coûte environ 2 à 3 fois un appel unique, donc réservez-la au raisonnement
difficile. `fuse` affiche aussi le coût en tokens par étape (panel / juge / synth) pour que
vous puissiez voir où les tokens d'un run vont réellement.

**Fusion sélective (activée par défaut, économise des tokens).** Le moteur sonde les
premiers `CHIMERA_FUSION_PROBE_K` modèles du panel (2 par défaut) et, quand leurs réponses
s'accordent étroitement, saute le reste du panel *et* le juge — synthétisant directement à
partir des réponses concordantes. La vérification d'accord est une comparaison textuelle
locale bon marché (aucun appel de modèle supplémentaire), donc un tour *en désaccord*
escalade vers le pipeline complet et coûte exactement la même chose qu'une fusion complète,
tandis qu'un tour *en accord* est moins cher. Ajustez le seuil avec
`CHIMERA_FUSION_AGREEMENT` (0–1, 0,8 par défaut), ou définissez `CHIMERA_FUSION_MODE=full`
(ou passez `--full`) pour toujours exécuter le panel + juge complets.

Pourquoi c'est le défaut : sur 3 runs de `chimera fusion-bench --tasks hard` (un panel payant
à 3 modèles) cela a réduit les tokens de **~20–28 %** et était correct sur **chaque** tour
qu'il a réellement court-circuité (16/16). La précision globale a oscillé entre 0 et −8,3 pp
d'un run à l'autre, mais cette variance atterrit entièrement dans le lot *escaladé* — où le
mode sélectif exécute le pipeline identique au mode complet — donc c'est de la
non-déterminisme de modèle, pas un coût de l'arrêt anticipé. Lancez le bench sur votre propre
charge de travail pour voir le compromis pour votre panel et vos tâches :

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **Choisissez des modèles de panel fiables.** La fusion ne rapporte que si chaque membre du
> panel répond effectivement. Évitez les slugs de modèle OpenRouter `:free` dans
> `CHIMERA_FUSION_PANEL` — ils sont limités en débit (HTTP 429) sous une charge réelle, et le
> panel se réduit silencieusement au seul modèle payant restant. Un trio bon marché et
> fiable : `openrouter/deepseek/deepseek-chat`, `openrouter/openai/gpt-4o-mini`,
> `openrouter/meta-llama/llama-3.3-70b-instruct`.

### Cartes de skills (cartes de raisonnement TRS, expérimental)

L'agent distille ce qu'il apprend en **cartes de raisonnement** — les cinq champs Trigger /
Do / Avoid / Check / Risk (plus des mots-clés de recherche) — à la fois à partir de succès
(une carte *pattern*) et d'échecs récurrents (une carte consultative *anti-pattern*). Quand
`CHIMERA_SKILL_CARDS=on`, `solve` récupère les k cartes pertinentes les plus pertinentes
(BM25 sur nom + description + triggers) et les injecte dans le contexte de raisonnement du
worker, si bien que l'agent réutilise ce qui a fonctionné et évite les modes d'échec connus.
Cela referme la boucle — auparavant, les skills apprises étaient stockées et jamais relues.

Désactivé par défaut : injecter des cartes ajoute des tokens de prompt, et les économies de
*tokens* de TRS viennent du raccourcissement des longues traces de raisonnement, donc sur des
tâches à réponse courte le bénéfice porte sur la précision, pas sur le coût. Ce n'est pas
hypothétique — sur la suite `hard` à réponses courtes (deepseek-v3.1 payant),
`skillcard-bench` a mesuré des cartes coûtant **+290 % de tokens** et **−8 pp de précision**
par rapport à l'absence de cartes : avec un modèle proche du plafond et aucune longue trace à
raccourcir, des cartes génériques sont une pure surcharge qui peut distraire. Activez les
cartes pour des charges de travail à **raisonnement long** (maths/code avec des traces
longues) où l'équation de tokens s'inverse, et mesurez toujours votre propre compromis
d'abord avec une vérification à vérité terrain :

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

Le bench rapporte la précision avec vs sans cartes, le delta de tokens, le taux de succès des
cartes (hit-rate), et la précision répartie par hit/miss, avec un verdict PASS quand la
précision avec cartes reste dans 1 pp de la référence sans cartes.

### Schémas d'outils compacts (expérimental)

Les schémas d'outils — en particulier ceux importés depuis des serveurs MCP ou des specs
OpenAPI — portent du bruit d'annotation (exemples, titres, valeurs par défaut, prose de
paramètre sur plusieurs phrases, corps de requête imbriqués) qui est renvoyé au modèle à
**chaque** étape ReAct. Avec `CHIMERA_COMPACT_SCHEMAS=on`, ce bruit est retiré et les
descriptions de paramètres élaguées au moment de l'annonce, **sans** toucher à quoi que ce
soit qui affecte un appel (le nom et la description de la fonction, et le `type` /
`properties` / `required` / `enum` de chaque schéma sont préservés). Les schémas canoniques
restent intacts — seule la copie envoyée au modèle rétrécit.

L'économie est la plus importante sur les jeux d'outils MCP/OpenAPI verbeux et s'accumule à
chaque étape ; les outils natifs sont déjà concis, donc leur réduction est faible. Mesurez
d'abord votre jeu d'outils (aucun appel de modèle — cela ne fait que compter des tokens) :

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

Désactivé par défaut. Comme la compaction ne retire que le bruit d'annotation (jamais la
structure), le seul risque est que le modèle ait un peu moins de prose pour choisir un
outil — cela reste donc conservateur, et vous devriez confirmer le comportement d'appel
d'outils sur votre charge de travail avant d'activer.

### `solve` — autonome Tier 2 (plan + verify-or-revert)

Planifie la tâche, exécute avec la boucle de l'agent, puis **vérifie avec une commande
exécutable**. Si la vérification échoue, il restaure le workspace et retente avec un retour.
Le vérificateur (code de sortie 0 = succès) est la vérité terrain.

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

Flags utiles :

| Flag | Signification |
|------|---------|
| `--verify "<cmd>"` | commande qui doit se terminer avec le code 0 (tests, un build, un linter) |
| `--workspace`, `-w` | où l'agent lit/écrit (`.` par défaut) |
| `--max-attempts N` | budget verify-or-revert (3 par défaut) |
| `--max-steps N` | étapes d'appel d'outils par tentative (8 par défaut) |
| `--fuse` | produit le **plan** via la fusion (raisonnement profond) |
| `--guard` | filtre chaque appel d'outil à travers le noyau de gouvernance |
| `--no-plan` / `--no-manager` | saute l'étape de planification / de révision |
| `--rubric` | le Manager juge via la **rubrique en cascade** (respect des instructions → factualité → rationalité) |
| `--no-remember` | n'écrit pas automatiquement un fait mémorisé en cas de succès |
| `--no-evolve-skills` | ne propose pas automatiquement une skill apprise quand une tâche récurre |
| `--isolate` | tourne dans un git worktree jetable ; les fichiers modifiés ne sont recopiés qu'en cas de succès |
| `--require-diff` | une tentative qui n'a modifié **aucun fichier** échoue et est retentée — pour une tâche de code, une explication n'est pas un correctif |
| `--keep-workspace` | en cas d'échec, laisse les modifications de la dernière tentative sur disque au lieu de les restaurer — pour quand un correcteur **externe** décide du pass/fail |
| `--diff-feedback` | montre à une tentative échouée son propre diff restauré, présenté comme un chemin à ne pas reprendre |
| `--stagnation-fuzzy` | fait correspondre les signatures d'échec répété de façon approximative, pour que le pivot anti-blocage se déclenche sur des échecs de même cause dont le libellé diffère |

> **À propos de `--max-steps`.** Le défaut de 8 est calibré pour de petits workspaces. Sur un
> **grand dépôt, c'est la contrainte bloquante**, pas le modèle : le run 1 de SWE-bench a
> obtenu un 0,0 pp exact à 8 étapes contre un checkout de 250 Mo, et la même configuration à
> **30 étapes** a fait passer le taux de patch de la référence de 47 % à 74 %
> ([`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)). Si l'agent explore puis
> termine sans éditer, augmentez d'abord ce paramètre.

> **`--require-diff` et `--keep-workspace` sont pour la notation externe.** `solve` est
> verify-or-revert : quand c'est *lui* qui possède la décision pass/fail, restaurer une
> tentative échouée est correct. Quand quelque chose d'autre la possède — un job CI, un
> harness de benchmark, un humain qui relit le diff — `--keep-workspace` empêche le travail
> de l'agent d'être annulé avant que ce juge ne le voie jamais, et `--require-diff` empêche
> qu'une explication confiante soit notée comme un changement accompli. Les deux sont
> **désactivés par défaut**.

**`solve` apprend d'un run à l'autre.** Chaque run alimente une boucle comportementale
fermée, entièrement verrouillée par verify-or-revert pour que seul le travail vérifié ait un
effet : (1) les **leçons** pertinentes de tentatives passées (les échecs sont favorisés) sont
repliées dans le plan/prompt, et la **première étape fautive** d'une tentative échouée est
localisée et injectée dans la relance ; (2) en cas de succès vérifié, un fait de **mémoire**
dédupliqué est écrit (rappelé plus tard par `chat`/`crew`) ; et (3) quand un pattern de tâche
récurre (≥ 2 succès antérieurs), une **skill** réutilisable est proposée — à travers le panel
de fusion et conservée par **transférabilité** inter-modèle quand `--fuse` est activé — et
n'est gardée que si elle passe la validation de gouvernance et un test de fumée exécutable.

### `crew` — multi-agent Tier 3

Une équipe d'agents de rôle collabore sur une tâche et un superviseur synthétise la réponse
finale.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — équipe SDLC (plan → build → test → review)

Un pipeline de cycle de vie logiciel pré-assemblé avec **verify-or-revert** à l'étape de
test : `plan` décompose la tâche, `build` l'implémente, `test` exécute le vérificateur (en
restaurant et en relançant le build en cas d'échec), et un réviseur critique le résultat.

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

Chaque étape s'affiche avec un ✓/✗ ; le run n'est `success` que si le vérificateur de l'étape
de test a réussi.

### `meta` — des agents qui construisent des agents

Conçoit un plan directeur d'agent spécialisé (nom, outils, prompt de rôle) pour une tâche.

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — verdict de gouvernance

Affiche la décision du noyau de confiance (allow / warn / review / block) pour une action.

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — benchmark d'évolution continue

Mesure si la performance *tient* sur une chaîne de tâches (la preuve anti-dégradation) :
taux de réussite global, première moitié vs seconde moitié, plus longue série.

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

Le rapport porte aussi un indicateur de dégradation **statistiquement honnête** : plutôt que
de faire confiance à une simple soustraction première-moins-seconde-moitié (sur une chaîne
courte, une variation de 0,2 est généralement du bruit), `degraded_significant` ne vaut `1.0`
que quand un intervalle de confiance de Wilson sur la chute exclut zéro, `-1.0` quand
l'échantillon est trop petit pour se prononcer, et `0.0` sinon — plus les bornes
`degradation_ci_low/high`. Séparément, `CHIMERA_SKILL_ACCEPT_MODE=wilson` conditionne la
décision d'acceptation de skill inter-modèle à la borne de confiance *inférieure* du taux de
transfert (pour qu'un 2-sur-3 chanceux ne compte plus) ; le défaut `point` garde le taux
brut, car la borne de Wilson est stricte sur de tout petits panels.

### `sandbox-bench` — notation de l'état + des effets de bord

Les benchs textuels notent la *réponse* du modèle ; celui-ci note ce que l'agent a **fait**.
Chaque tâche tourne dans un répertoire sandbox isolé, et le harness compare l'état final des
fichiers à l'objectif (n'importe quel chemin autorisé, façon résultat) **et** compte
séparément les *effets de bord nuisibles* — des mutations en dehors de l'ensemble autorisé
déclaré pour la tâche. Un agent qui produit le bon résultat tout en écrasant un fichier sans
rapport est donc détecté, pas noté comme un succès propre.

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

Rapporte `pass_rate` et `side_effect_rate`. Il fournit la *méthodologie* (une `StatefulTask`
avec `goal_check` + un ensemble `allowed` de mutations), pas une grande suite de tâches —
écrivez des tâches pour vos propres outils. Les correcteurs textuels existants restent
corrects pour du travail pur Q&A.

### `memory` — mémoire à long terme curatée

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

Le rappel passe par une **barrière d'admission** (une frontière de confiance) : une mémoire
rappelée n'entre dans le prompt que si elle est pertinente *et* exempte de texte de
surcharge/injection (défense contre le jailbreak basé sur la mémoire). `memory prune` oublie
sous un budget selon un modèle de **valeur** multi-facteurs (récence, spécificité, nature,
curation, fiabilité) — pas un seul indice.

La **couche graphe** extrait des triplets `(source, relation, cible)` de vos mémoires
(`PassaPro uses Supabase`, `Alex prefers TypeScript`), pour que les faits puissent être
rappelés par entité, pas seulement par mot-clé.

### `cron` — tâches planifiées & SOP événementiels

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — tableau de tâches avec voies de workers

Un tableau (`backlog → doing → review → done`) où chaque carte nomme une *voie* (lane) qui
la distribue à la pile d'agent : `solve` (autonome Tier 2, verify-or-revert) ou `crew`
(pipeline de rôle Tier 3). La vue opérationnelle de la boucle que l'agent exécute déjà.

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` fait avancer chaque carte de backlog → doing → done (succès) ou → review (nécessite de
l'attention). `learn` réutilise le détecteur de récurrence du cron-learner pour mettre en
file les tâches que l'agent répète (dédupliquées par rapport au tableau) — planifiez-le pour
remplir automatiquement le backlog.

### `workflow` — boucles conçues (Loop Engineering)

Rédigez une boucle autonome en YAML plutôt qu'en prompt ad hoc. Chaque étape `uses` une
capacité (`run` / `shell` / `solve` / `crew` / `lifecycle`), peut être conditionnée à
l'étape précédente (`when: prev_succeeded | prev_failed`), et peut boucler (`repeat`,
`until: success`).

```yaml
# examples/workflow.yaml
name: build-and-report
steps:
  - name: build
    uses: solve
    with: { task: "Create greeting.py with greet(name)", verify: "python -c \"import greeting\"" }
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with: { prompt: "One-line changelog for greet()" }
```

```bash
uv run chimera workflow examples/workflow.yaml --workspace ./scratch
```

### `drift` — barrière de dérive spec↔code

Gardez une spec et le code alignés. Une spec est un petit YAML d'exigences (`defines` un
symbole / `contains` une regex / `absent` une regex / `command` se termine avec le code 0).
La barrière se termine avec un code non nul en cas de dérive, elle sert donc aussi de
vérificateur.

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — importer depuis un autre agent

Apporte la **config + les skills** depuis Hermes ou OpenClaw, et avec `--apply` **fusionne
aussi la mémoire à long terme** (dédupliquée, non destructive). Le défaut est un aperçu à
blanc (dry-run).

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

La fusion de mémoire rapporte des compteurs `{ADD, UPDATE, NOOP}` — les doublons deviennent
`NOOP`, relancer est donc sans risque.

### `evolve` — évolution de modèle opt-in (avancé)

`chimera solve --collect` (activé par défaut) journalise chaque run comme une trajectoire.
Les commandes `evolve` transforment cela en jeux de données prêts pour l'entraînement et une
recette LoRA exécutable. **L'entraînement est externe et opt-in** — il change les poids du
modèle, donc cela ne se produit jamais automatiquement ; Chimera prépare les données et un
script, puis s'arrête.

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` accepte des réglages de recette : `--min-steps N` ne garde que les traces à long
horizon, `--diverse` ne garde au plus qu'un exemple par tâche (la diversité des tâches est le
goulet d'étranglement de la curation), et `--min-process P` (SkillCoach) ne garde que les
traces dont le score de *suivi d'étapes* ≥ P — la fraction d'étapes d'outil ayant produit un
résultat visible réussi — pour qu'un succès chanceux qui a pataugé à travers des appels
d'outils échoués ne soit pas utilisé pour l'entraînement. Les événements par étape derrière
ce score sont capturés automatiquement à chaque run `solve` ; le filtre est désactivé par
défaut (`CHIMERA_SFT_MIN_PROCESS` définit un défaut global). `evolve tune` est différent de
l'entraînement — il exécute une **méta-recherche** sur la *spec* de l'agent (modèle, prompt
système, budget d'étapes, panel, profondeur de mémoire), en notant chaque candidat sur les
scénarios quotidiens et en ne gardant une modification qu'en cas de **non-régression**. Il
appelle des modèles mais ne change jamais les poids, il est donc sûr à exécuter à tout
moment.

Ensuite, pour réellement entraîner, sur un GPU (ou Colab) : `pip install
chimera-agent[train]` (ou le `requirements.txt` de la recette) puis `python
recipe/train.py`. Pointez `CHIMERA_DEFAULT_MODEL` vers le modèle de base + l'adaptateur lors
du service.

### `pet` — un compagnon virtuel

Un petit compagnon persistant dont les statistiques évoluent pendant votre absence. Aucune
clé nécessaire.

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## Astuces

- **Outils vs raisonnement.** Les tours d'appel d'outils utilisent toujours un seul modèle
  (la fusion ne peut pas appeler d'outils) ; la fusion est réservée au raisonnement profond
  sans outil.
- **Inspecter ce qui s'est passé.** `CHIMERA_LOG_LEVEL=DEBUG` fait remonter les logs de
  routage et d'engagement de la fusion.
- **Garder des tests honnêtes.** Une bonne commande `--verify` (une vraie suite de tests)
  rend `solve` fiable — c'est la vérité terrain exécutable à laquelle l'agent est tenu.
