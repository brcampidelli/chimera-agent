---
source_sha256: f08c31cf980c0d86795fe456d5f9ed6871b58325c9e429e93f48c71b6e998356
---

# Recettes

De vrais workflows exécutables qui accomplissent quelque chose d'utile de bout en bout avec les
outils intégrés. Lancez-en n'importe lequel avec `chimera workflow <file> -w <workspace>`. Les
sources complètes vivent dans le dossier
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples).

## Entièrement local, sans clé API (Ollama)

Faites tourner Chimera contre un modèle sur votre propre machine — aucune clé, rien ne sort de
la machine. Installez [Ollama](https://ollama.com), récupérez un modèle, puis pointez Chimera
dessus :

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_DEFAULT_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera agent "Summarise this file in 3 bullets" -w .
```

C'est tout — pas de `OPENROUTER_API_KEY`, pas de cloud. La barrière d'identifiants reconnaît
`ollama/…` (et `ollama_chat/…`) comme un runtime local et laisse passer. Si Ollama tourne
ailleurs, définissez `CHIMERA_OLLAMA_BASE_URL=http://host:11434` (`http://localhost:11434` par
défaut).

Les modèles locaux sont plus petits, donc c'est l'extrémité *faible* de la plage
[goldilocks](../bench/local_lift/RESULTS.md) — bien adapté à `chimera solve` (plan +
verify-or-revert aide un modèle faible) et à la confidentialité hors ligne, moins pour du
raisonnement frontière en un coup. Combinez : un défaut local avec des
`CHIMERA_FALLBACK_MODELS` en cloud pour les appels difficiles.

## Triage d'e-mails

Lit votre boîte de réception, classe `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`, écrit un
digest de dix secondes. En lecture seule — rien n'est supprimé, déplacé ou envoyé.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Nécessite des identifiants IMAP. Configuration + planification quotidienne :
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Brief de recherche quotidien

Un sujet en entrée, un brief sourcé en 5 points + un digest de 3 lignes en sortie (arxiv
toujours ; recherche web si une clé Tavily est définie).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Surveillance de dépôt (repo watchdog)

Exécute la suite de tests d'un dépôt et écrit un rapport de santé nommant les tests en échec.
En lecture seule sauf le rapport.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Lire des documents (PDF, DOCX, XLSX…)

L'agent lit du texte brut par défaut. Pour de vrais documents — PDF, Word, PowerPoint, Excel,
HTML, CSV, EPUB — installez l'extra optionnel et il gagne un outil `read_document` qui convertit
n'importe lequel d'entre eux en Markdown :

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Pointez ensuite une tâche vers un fichier : *« Résume report.pdf en 5 points. »* Sans l'extra,
`read_document` renvoie une indication d'installation en une ligne plutôt que d'échouer.

## Naviguer sur le web (naviguer, lire + agir)

L'outil `browser` est **intégré** — il pilote un vrai Chromium (il voit donc les pages rendues
en JavaScript que le simple `http_get` ne peut pas voir). Playwright est fourni avec Chimera ;
le binaire Chromium d'environ 150 Mo est **téléchargé automatiquement la première fois que vous
utilisez le navigateur** (une étape ponctuelle que pip ne peut pas faire pour vous). Aucune
étape d'installation requise :

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

L'outil `browser` a ces actions :

- **`navigate` / `read`** — ouvre une URL et liste les éléments *interactifs* de la page sous
  la forme `[ref] role: name` (liens, boutons, champs), pour que l'agent clique/tape par `ref`,
  pas par pixel.
- **`read_text`** — le **texte intégral rendu** de la page, pour lire/rechercher un article, un
  document ou un résultat. Avec l'extra `documents` c'est du **Markdown** propre (titres, liens,
  listes préservés via MarkItDown) ; sans, le texte visible brut. Passez un `url` optionnel pour
  ouvrir + lire en une seule étape.
- **`find`** — recherche une requête dans le texte rendu et récupère les lignes correspondantes.
- **`click` / `type` / `back`** — pilote la page par `ref`.

`CHIMERA_BROWSER_HEADLESS=false` fait tourner Chromium en mode visible pour le débogage.

Le contenu de page est **non fiable** : chaque résultat est clôturé comme donnée (data-fenced)
et l'outil contamine le run, donc préférez `solve --taint --guard` lors de la navigation et
extrayez les champs structurés via le lecteur en quarantaine plutôt que d'agir sur le texte brut
de la page. Sans l'extra `documents`, `read_text` fonctionne quand même — juste en texte brut
au lieu de Markdown.

## Rechercher un sujet (recherche + lecture)

Combinez la recherche web avec le `read_text` du navigateur pour rechercher quelque chose et
obtenir un brief sourcé — `web_search` (nécessite `TAVILY_API_KEY`) trouve les pages,
`browser read_text` lit chacune d'elles (y compris les sites riches en JS), et `deliver` rédige
le brief :

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

Pour une version prête à l'emploi avec une vérification exécutable par étape, voir
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief)
— elle utilise `arxiv_search` + `web_search` par défaut, et avec l'extra `browser` installé
l'agent peut aussi faire `read_text` de pages entières au lieu de s'arrêter aux extraits de
recherche.

## Scraping & extraction structurée sûre

Deux outils intégrés transforment n'importe quelle page en données propres, prêtes pour un LLM
— aucun extra à installer :

- **`scrape`** — récupère une URL et renvoie du **Markdown + métadonnées** propres. Il parcourt
  une cascade sensible au coût : un simple GET HTTP d'abord, escaladant vers le **navigateur**
  intégré (rendu JS) si la page revient vide, et — seulement si vous définissez
  `FIRECRAWL_API_KEY` — repliant sur **Firecrawl** pour les pages lourdement anti-bot.
  `render=http|browser|firecrawl` force un backend spécifique ; `include_links` renvoie aussi
  les liens de la page.
- **`extract`** — extrait des champs spécifiques sous forme de **JSON validé**, en toute
  sécurité. Donnez-lui une `url` (ou un `content`) et une liste de `fields` (par ex.
  `["title", "price", "author"]`) et il renvoie *uniquement* ces champs. Fait crucial, il lit la
  page à travers le **lecteur en quarantaine** de Chimera — un modèle sans outils dont la sortie
  est validée par schéma — donc **des instructions cachées dans la page ne peuvent pas
  détourner l'agent**. C'est la garantie de sécurité que Firecrawl/ScrapeGraphAI ne vous donnent
  pas : une page hostile peut au pire renvoyer une valeur erronée, jamais une nouvelle
  instruction. Les pages volumineuses sont découpées en morceaux et fusionnées, s'arrêtant tôt
  dès que chaque champ est rempli pour plafonner le coût. Pour un **modèle de page connu**,
  passez `selectors` (champ → CSS, par ex.
  `{"price": ".price", "link": "a.more::attr(href)"}`) et ces champs sont extraits **de façon
  déterministe — gratuit, sans LLM** — le LLM sûr n'étant utilisé que pour les champs qu'un
  sélecteur n'a pas remplis.

```bash
uv run chimera agent "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera agent "extract the fields title, price, availability from https://example.com/product --taint"
```

Pour des sites entiers, il y a deux verbes supplémentaires :

- **`map`** — liste les URLs d'un site à moindre coût (lit le sitemap quand il y en a un, sinon
  scanne les liens de la page). Filtre optionnel par mot-clé `search`. Lancez ceci pour
  cadrer un site avant de le crawler.
- **`crawl`** — suit les liens depuis une URL de départ et renvoie le Markdown propre de
  chaque page. Borné par `limit` et `max_depth`, même domaine par défaut, et **respectueux du
  robots.txt** (il obéit à `Disallow` et `Crawl-delay`). `include`/`exclude` sont des motifs
  glob d'URL. Les crawls longs sont **reprenables** : la frontière est sauvegardée sur disque
  après chaque page, si bien qu'un crawl interrompu à la page N continue depuis N+1 au run
  suivant (`resume=true` par défaut).

```bash
uv run chimera agent "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Tout est clôturé comme donnée et contamine le run (c'est du contenu web non fiable), donc
`solve --taint --guard` est la façon sûre d'agir dessus. Le repli optionnel vers Firecrawl n'est
utilisé *que* quand le moteur intégré ne peut pas récupérer une page et que la clé est
définie — Chimera scrape la grande majorité du web lui-même, sans service externe.

## Audio : transcription voix-vers-texte

Chimera peut transformer la parole en texte — le partenaire symétrique de ses outils de
génération d'images et de synthèse vocale. Il **orchestre un modèle Whisper** (il n'en entraîne
pas) : l'outil `transcribe_audio` utilise le **faster-whisper** local si vous installez l'extra
`stt` (hors ligne/privé), sinon l'API Whisper hébergée d'OpenAI (nécessite une clé OpenAI) :

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera agent "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> Une note sur le périmètre, dans l'esprit honnête de ce projet : Chimera est un **agent**, pas
> un modèle. Il peut *utiliser* la transcription vocale, la génération d'images, la vision par
> ordinateur, ou du ML classique — en appelant une API ou en exécutant une bibliothèque dans son
> sandbox de code — mais il ne *réimplémente* pas (et ne peut pas raisonnablement le faire)
> Whisper, Stable Diffusion, PyTorch, ou OpenCV. Pour la data science / le ML, le sandbox
> `execute_code` laisse déjà l'agent écrire et exécuter du Python contre scikit-learn, pandas,
> OpenCV, etc. L'orchestration multiplie l'agent ; la réimplémentation ne produirait qu'une
> copie plus lente.

## Télécharger une vidéo ou son audio

L'outil `download_media` récupère une vidéo (ou juste son audio) depuis YouTube et plus de
1000 autres sites dans le workspace. Il enveloppe **yt-dlp** (activement maintenu, gère les
changements de chiffrement/format/vérification d'âge qui coulent les scrapers mono-site comme
pytube). Opt-in ; l'extraction audio nécessite aussi `ffmpeg` dans le PATH :

```bash
uv sync --extra media-dl
uv run chimera agent "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Se combine naturellement avec `transcribe_audio` ci-dessus : télécharger → transcrire →
résumer, tout en un seul run.

## Analyse de données / ML (la skill `data_analysis`)

Chimera ne réimplémente pas scikit-learn — il **écrit du code pandas/sklearn correct et
l'exécute** dans le sandbox `execute_code`. La skill `data_analysis` nomme cette capacité :
donnez-lui une tâche et un jeu de données et elle produit un script autonome (charger →
explorer → modéliser → évaluer) que l'agent exécute ensuite.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera agent "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Génération d'images (hébergée ou entièrement locale)

`generate_image` utilise l'API image d'OpenAI par défaut. Pour une configuration
**hors ligne / privée**, définissez `CHIMERA_IMAGE_BACKEND=local` et installez l'extra
`imagegen-local` (lourd, dépendant d'un GPU) — Chimera fait alors tourner **FLUX.1-schnell**
(Apache-2.0) via `diffusers` localement. `auto` (le défaut) n'utilise le local que quand aucune
clé OpenAI n'est présente.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera agent "generate an image of a fox in a snowy forest"
```

> Même périmètre honnête que ci-dessus : Chimera *exécute* un modèle de diffusion ici ; il n'en
> entraîne pas. La génération vidéo (par ex. CogVideo) n'est délibérément **pas** intégrée —
> c'est un modèle entraîné lourd, pas quelque chose qu'un agent devrait porter dans sa base ;
> tournez-vous vers une API hébergée si vous en avez un jour besoin. La vision par ordinateur
> (OpenCV) n'a besoin d'aucun outil dédié — l'agent fait déjà `import cv2` dans le sandbox de
> code.

## Graphiques & visualisation de données

Deux façons complémentaires de faire un graphique — toutes deux honnêtes sur leur périmètre
(Chimera *utilise* des bibliothèques de traçage ; il ne réimplémente pas
matplotlib/plotly/bokeh) :

**1. La skill `data_visualization` — écrire du code de graphique, l'exécuter dans le sandbox.**
Couvre *tout* (figures personnalisées/de publication, 3D, n'importe quoi) : la skill produit un
script autonome utilisant matplotlib/seaborn (PNG/SVG statique) ou plotly (HTML interactif),
avec le backend headless (`matplotlib.use("Agg")`) et la discipline de sauvegarde vers le
workspace intégrées.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera agent "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. L'outil `render_chart` — une spec Vega-Lite sûre et déclarative.** Une spec Vega-Lite est
du **JSON inerte, pas du code** : inspectable, en forme de schéma, et re-rendable — une
gouvernance plus forte que l'exécution de code généré, pour les graphiques standards que
Vega-Lite couvre (barres/lignes/nuage de points/histogramme/heatmap/à facettes…). **La sortie
HTML ne nécessite aucun extra** (elle embarque la spec + le CDN Vega) ; le PNG/SVG utilise
l'extra optionnel `viz-vega` (`vl-convert-python`).

```bash
uv run chimera agent "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Périmètre honnête : plotly enveloppe plotly.js, bokeh est environ moitié TypeScript, le
> moteur de rendu de matplotlib est en C++, et seaborn est une fine couche au-dessus de
> matplotlib — tous des frameworks qu'un agent devrait *appeler*, pas réécrire. Le sandbox de
> code les importe déjà ; la skill ne fait que nommer la capacité et gérer les pièges du mode
> headless. Vega-Lite est l'exception qui mérite un outil dédié parce que son artefact est de la
> donnée déclarative sûre.

## Planifier n'importe laquelle d'entre elles

Chaque recette peut tourner sur un cron et livrer dans un chat :

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

Voir [Déployer](deploy.md) pour la passerelle de messagerie et la configuration 24/7.
