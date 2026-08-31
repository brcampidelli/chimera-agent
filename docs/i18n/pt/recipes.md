---
source_sha256: f08c31cf980c0d86795fe456d5f9ed6871b58325c9e429e93f48c71b6e998356
---

# Recipes

Workflows reais e executáveis que fazem algo útil de ponta a ponta com as tools embutidas. Rode
qualquer uma com `chimera workflow <file> -w <workspace>`. As fontes completas vivem na pasta
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples).

## Totalmente local, sem chave de API (Ollama)

Rode o Chimera contra um modelo na sua própria máquina — sem chave, nada sai da caixa. Instale o
[Ollama](https://ollama.com), baixe um modelo, e aponte o Chimera para ele:

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_DEFAULT_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera agent "Summarise this file in 3 bullets" -w .
```

Só isso — sem `OPENROUTER_API_KEY`, sem nuvem. O gate de credenciais reconhece `ollama/…` (e
`ollama_chat/…`) como um runtime local e deixa passar. Se o Ollama roda em outro lugar, defina
`CHIMERA_OLLAMA_BASE_URL=http://host:11434` (padrão `http://127.0.0.1:11434`).

Modelos locais são menores, então esta é a ponta *fraca* da faixa
[goldilocks](../bench/local_lift/RESULTS.md) — um bom encaixe para `chimera solve` (plano +
verificar-ou-reverter ajuda um modelo fraco) e para privacidade offline, menos para raciocínio de
fronteira em uma única tacada. Misture e combine: um padrão local com
`CHIMERA_FALLBACK_MODELS` na nuvem para as chamadas difíceis.

## Triagem de e-mail

Lê sua caixa de entrada, classifica `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`, escreve um
resumo de dez segundos. Somente leitura — nada é apagado, movido ou enviado.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Precisa de credenciais IMAP. Configuração + agendamento diário:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Resumo diário de pesquisa

Um tópico entra, um resumo de 5 pontos com fontes + um resumo de 3 linhas sai (arxiv sempre; busca
web se uma chave Tavily estiver definida).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Watchdog de repositório

Roda a suíte de testes de um repositório e escreve um relatório de saúde nomeando quaisquer testes
que falharem. Somente leitura, exceto pelo relatório.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Lendo documentos (PDF, DOCX, XLSX…)

O agente lê texto puro nativamente. Para documentos reais — PDF, Word, PowerPoint, Excel, HTML,
CSV, EPUB — instale o extra opcional e ele ganha uma tool `read_document` que converte qualquer um
deles para Markdown:

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Depois aponte uma tarefa para um arquivo: *"Resuma report.pdf em 5 tópicos."* Sem o extra,
`read_document` retorna uma dica de instalação de uma linha em vez de falhar.

## Navegando na web (navegar, ler + agir)

A tool `browser` é **embutida** — ela conduz um Chromium de verdade (então enxerga páginas
renderizadas em JavaScript que o `http_get` puro não consegue). O Playwright vem junto com o
Chimera; o binário do Chromium de ~150MB é **baixado automaticamente na primeira vez que você usa
o browser** (um passo único que o pip não consegue fazer por você). Nenhum passo de instalação é
necessário:

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

A tool `browser` tem estas ações:

- **`navigate` / `read`** — abre uma URL e lista os elementos *interativos* da página como
  `[ref] role: name` (links, botões, campos), então o agente clica/digita por `ref`, não por
  pixel.
- **`read_text`** — o **texto completo renderizado** da página, para ler/pesquisar um artigo,
  documento ou resultado. Com o extra `documents`, sai como **Markdown** limpo (cabeçalhos, links,
  listas preservados via MarkItDown); sem ele, o texto visível puro. Passe um `url` opcional para
  abrir + ler em um único passo.
- **`find`** — busca no texto renderizado por uma query e retorna as linhas correspondentes.
- **`click` / `type` / `back`** — conduz a página por `ref`.

`CHIMERA_BROWSER_HEADLESS=false` roda o Chromium com interface visível para depuração.

O conteúdo da página é **não confiável**: todo resultado é cercado como dado e a tool contamina a
execução, então prefira `solve --taint --guard` ao navegar e extraia campos estruturados através
do leitor quarentenado em vez de agir sobre o texto bruto da página. Sem o extra `documents`, o
`read_text` continua funcionando — só que como texto puro em vez de Markdown.

## Pesquisando um tópico (buscar + ler)

Combine busca web com o `read_text` do browser para pesquisar algo e obter um resumo com fontes —
`web_search` (precisa de `TAVILY_API_KEY`) encontra as páginas, `browser read_text` lê
cada uma (inclusive sites pesados em JS), e `deliver` escreve o resumo:

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

Para uma versão pronta com uma checagem executável por passo, veja
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief) — ela usa `arxiv_search` +
`web_search` nativamente, e com o extra `browser` instalado o agente também pode fazer `read_text`
de páginas completas em vez de parar nos trechos da busca.

## Scraping & extração estruturada segura

Duas tools embutidas transformam qualquer página em dados limpos, prontos para LLM — nenhum extra
para instalar:

- **`scrape`** — busca uma URL e retorna **Markdown + metadados** limpos. Percorre uma cascata
  consciente de custo: primeiro um GET HTTP puro, escalando para o **browser** embutido
  (renderização JS) se a página voltar vazia, e — só se você definir `FIRECRAWL_API_KEY` —
  recorrendo ao **Firecrawl** para páginas pesadas com anti-bot. `render=http|browser|firecrawl`
  força um backend específico; `include_links` também retorna os links da página.
- **`extract`** — extrai campos específicos como **JSON validado**, com segurança. Dê a ela uma
  `url` (ou `content`) e uma lista de `fields` (ex.: `["title", "price", "author"]`) e ela retorna
  *só* esses campos. Crucialmente, ela lê a página através do **leitor quarentenado** do Chimera —
  um modelo sem tools cuja saída é validada por schema — então **instruções escondidas na página
  não conseguem sequestrar o agente**. Essa é a garantia de segurança que Firecrawl/ScrapeGraphAI
  não te dão: uma página hostil pode no máximo retornar um valor errado, nunca uma instrução nova.
  Páginas grandes são divididas em pedaços e mescladas, parando cedo assim que todo campo é
  preenchido para limitar o custo. Para um **template de página conhecido**, passe `selectors`
  (campo → CSS, ex.: `{"price": ".price", "link": "a.more::attr(href)"}`) e esses campos são
  extraídos **de forma determinística — grátis, sem LLM** — com o LLM seguro usado só para os
  campos que um seletor não preencheu.

```bash
uv run chimera agent "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera agent "extract the fields title, price, availability from https://example.com/product --taint"
```

Para sites inteiros há mais dois verbos:

- **`map`** — lista as URLs de um site de forma barata (lê o sitemap quando existe um, senão
  varre os links da página). Filtro opcional de palavra-chave `search`. Rode isso para mapear um
  site antes de fazer crawl nele.
- **`crawl`** — segue links a partir de uma URL semente e retorna o Markdown limpo de cada página.
  Limitado por `limit` e `max_depth`, restrito ao mesmo domínio por padrão, e **respeitando
  robots.txt** (obedece `Disallow` e `Crawl-delay`). `include`/`exclude` são padrões glob de URL.
  Crawls longos são **retomáveis**: a fronteira é salva em disco após cada página, então um crawl
  interrompido na página N continua da N+1 na próxima execução (`resume=true` por padrão).

```bash
uv run chimera agent "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Tudo é cercado como dado e contamina a execução (é conteúdo web não confiável), então
`solve --taint --guard` é a forma segura de agir sobre ele. O fallback opcional do Firecrawl é
usado *só* quando o motor embutido não consegue buscar uma página e a chave está definida — o
Chimera raspa a grande maioria da web sozinho, sem serviço externo.

## Áudio: fala-para-texto (transcrição)

O Chimera consegue transformar fala em texto — o parceiro simétrico das suas tools de geração de
imagem e texto-para-fala. Ele **orquestra um modelo Whisper** (não treina um): a tool
`transcribe_audio` usa o **faster-whisper** local se você instalar o extra `stt`
(offline/privado), senão a API hospedada Whisper da OpenAI (precisa de uma chave OpenAI):

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera agent "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> Uma nota sobre escopo, no espírito honesto deste projeto: o Chimera é um **agente**, não um
> modelo. Ele pode *usar* fala-para-texto, geração de imagem, visão computacional, ou ML clássico
> — chamando uma API ou rodando uma biblioteca no seu sandbox de código — mas não (e não faria
> sentido) *reimplementar* o Whisper, Stable Diffusion, PyTorch, ou OpenCV. Para ciência de dados
> / ML, o sandbox `execute_code` já deixa o agente escrever e rodar Python contra scikit-learn,
> pandas, OpenCV, etc. A orquestração multiplica o agente; a reimplementação só produziria uma
> cópia mais lenta.

## Baixar um vídeo ou seu áudio

A tool `download_media` puxa um vídeo (ou só seu áudio) do YouTube e mais de 1000 outros sites
para o workspace. Ela envolve o **yt-dlp** (mantido ativamente, lida com a mudança constante de
cifra/formato/age-gate que afunda scrapers de site único como o pytube). Opt-in; a extração de
áudio também precisa do `ffmpeg` no PATH:

```bash
uv sync --extra media-dl
uv run chimera agent "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Combina naturalmente com o `transcribe_audio` acima: baixar → transcrever → resumir, tudo em uma
única execução.

## Análise de dados / ML (a skill `data_analysis`)

O Chimera não reimplementa o scikit-learn — ele **escreve código pandas/sklearn correto e o
executa** no sandbox `execute_code`. A skill `data_analysis` nomeia essa capacidade: dê a ela uma
tarefa e um dataset e ela emite um script autocontido (carregar → explorar → modelar → avaliar)
que o agente então executa.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera agent "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Geração de imagem (hospedada ou totalmente local)

`generate_image` usa a API de imagem da OpenAI por padrão. Para uma configuração
**offline/privada**, defina `CHIMERA_IMAGE_BACKEND=local` e instale o extra (pesado, dependente de
GPU) `imagegen-local` — o Chimera então roda o **FLUX.1-schnell** (Apache-2.0) via `diffusers`
localmente. `auto` (o padrão) usa local só quando nenhuma chave OpenAI está presente.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera agent "generate an image of a fox in a snowy forest"
```

> Mesmo escopo honesto de antes: o Chimera *roda* um modelo de difusão aqui; ele não treina um. A
> geração de vídeo (ex.: CogVideo) deliberadamente **não** está embutida — é um modelo treinado
> pesado, não algo que um agente deveria carregar na sua base; recorra a uma API hospedada se
> algum dia precisar. Visão computacional (OpenCV) não precisa de tool dedicada — o agente já faz
> `import cv2` no sandbox de código.

## Gráficos & visualização de dados

Duas formas complementares de fazer um gráfico — ambas honestas sobre o escopo (o Chimera *usa*
bibliotecas de plotagem; ele não reimplementa matplotlib/plotly/bokeh):

**1. A skill `data_visualization` — escreve código de gráfico, roda no sandbox.** Cobre *tudo*
(figuras customizadas/de publicação, 3D, qualquer coisa): a skill emite um script autocontido
usando matplotlib/seaborn (PNG/SVG estático) ou plotly (HTML interativo), com o backend headless
(`matplotlib.use("Agg")`) e a disciplina de salvar no workspace já embutidos.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera agent "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. A tool `render_chart` — uma especificação Vega-Lite segura e declarativa.** Uma spec
Vega-Lite é **JSON inerte, não código**: inspecionável, com formato de schema, e re-renderizável —
uma história de governança mais forte do que executar código gerado, para os gráficos padrão que o
Vega-Lite cobre (barra/linha/dispersão/histograma/mapa de calor/facetado…). **A saída HTML não
precisa de extra** (ela embute a spec + o CDN do Vega); PNG/SVG usam o extra opcional `viz-vega`
(`vl-convert-python`).

```bash
uv run chimera agent "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Escopo honesto: o plotly envolve o plotly.js, o bokeh é ~metade TypeScript, o renderizador do
> matplotlib é C++, e o seaborn é uma camada fina sobre o matplotlib — todos frameworks que um
> agente deveria *chamar*, não reescrever. O sandbox de código já os importa; a skill só nomeia a
> capacidade e lida com as pegadinhas do modo headless. O Vega-Lite é a exceção que merece uma
> tool dedicada porque seu artefato é dado declarativo seguro.

## Agende qualquer uma delas

Toda recipe pode rodar em um cron e entregar em chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

Veja [Deploy](deploy.md) para o gateway de mensageria e a configuração 24/7.
