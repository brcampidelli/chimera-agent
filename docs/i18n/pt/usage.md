---
source_sha256: 4504ffb2fec03c1c651f531ab25212f512ab598ea09307542f0526a580d9965c
---

# Chimera — Guia de Uso

O Chimera é um agente auto-evolutivo, CLI-first, com um núcleo de raciocínio LLM-Fusion.
Este guia cobre instalação, configuração, e todo comando com exemplos.

> Novo no projeto? Leia primeiro a [visão geral da arquitetura](architecture.md).

---

## Instalação

O Chimera usa o [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

Todo comando abaixo é executado como `uv run chimera <command>` (ou simplesmente
`chimera …` uma vez que o virtualenv do projeto esteja no seu PATH).

---

## Configuração

O Chimera é agnóstico de provedor via [LiteLLM](https://docs.litellm.ai/). Coloque
suas chaves e escolhas de modelo em um `.env` local (ele é ignorado pelo git — nunca o commite):

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

Outros ajustes: `CHIMERA_HOME` (diretório de estado, padrão `.chimera`), `CHIMERA_LOG_LEVEL`
(`INFO` / `DEBUG`), `CHIMERA_CACHE` (`on`/`off`, padrão off — armazena em cache completions
idênticas sem tool para pular chamadas de API repetidas), e `CHIMERA_AUTO_FUSE` (`on`/`off`,
padrão off — funde automaticamente turnos profundos ou **sensíveis a erro** em `solve`/`crew`
sem um `--fuse` explícito; o roteador consciente de custo continua mantendo turnos
baratos/com tool em modelo único). O roteador reconhece prompts de resposta exata
(aritmética, contagem, operações com dígitos) nos principais idiomas do projeto
(en/pt/es/de/fr/zh/ja), então um passo curto e crítico ganha a proteção da fusão mesmo
quando é curto demais para acionar o gate de tamanho.

**Provedores, fallback & self-hosted.** Qualquer slug `provider/model` do LiteLLM
funciona (`openai/…`, `anthropic/…`, `gemini/…`, `ollama_chat/…`, `openrouter/…`, …). Para um
servidor self-hosted / compatível com OpenAI (Ollama, vLLM), defina `CHIMERA_API_BASE`
(ex.: `http://127.0.0.1:11434` com `CHIMERA_DEFAULT_MODEL=ollama_chat/llama3`). Use
`ollama_chat/` em vez de `ollama/`: o prefixo `ollama/` passa pelo endpoint generate do Ollama,
que não consegue chamar ferramentas. Defina
`CHIMERA_FALLBACK_MODELS` (separado por vírgula) para trocar para outro modelo se o
primário der erro. Em `chat`/`tui`, `/model <slug>` troca o modelo no meio da sessão.

**Pools de credenciais.** Dê a um provedor várias chaves com
`CHIMERA_<PROVIDER>_KEYS` (ex.: `CHIMERA_OPENROUTER_KEYS=key1,key2,key3`). O
gateway as roda em round-robin entre chamadas (distribuindo carga / limites de taxa) e,
dentro de uma única chamada, troca para a próxima chave se uma delas der erro. Um pool
substitui o `*_API_KEY` único desse provedor. *(Logins OAuth/assinatura — Copilot, Claude
Max, etc. — ainda não estão conectados; chaves de API e qualquer endpoint suportado pelo
LiteLLM estão.)*

Confira que tudo está configurado:

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**Funcionalidades opcionais.** Visão, o Modo Entregável e o Bichinho de estimação já vêm
embutidos. O resto (busca web, busca no X, geração de imagem, TTS/voz, Spotify, browser) são
slots pré-configurados: preencha a credencial correspondente no `.env` (ou instale a
dependência) e a capacidade se ativa. `chimera features` é o checklist ao vivo. A tool
`web_search` (Tavily) se autorregistra assim que `TAVILY_API_KEY` é definida — e é o
modelo para adicionar as outras (ou use o cliente MCP / o importador OpenAPI→tool).

> **Modelos gratuitos vs. pagos.** Modelos `:free` do OpenRouter não custam nada mas têm
> limite de taxa a montante — ok para um `run` rápido, instáveis para comandos de múltiplas
> chamadas como `fuse`/`solve`. Para uso real, um modelo pago barato (ex.:
> `deepseek/deepseek-chat-v3.1`, frações de centavo por chamada) é muito mais
> confiável.

---

## Comandos

### Status — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — assistente interativo de múltiplos turnos (seu braço direito)

Um REPL interativo com memória de conversa e uso de tools — o motorista do dia a dia.
Ele lembra memória de longo prazo relevante, encadeia a conversa entre turnos e **salva a
thread depois de cada turno** em `<home>/sessions`, então a próxima execução continua de onde
você parou. `chimera sessions` lista as threads salvas; `chimera sessions --delete <id>`
apaga uma.

```bash
uv run chimera chat                        # resume the newest thread; /exit to quit
uv run chimera chat --new                  # start a fresh thread instead of resuming
uv run chimera chat --session standup      # -s: resume, or name, one thread by id
uv run chimera chat --no-memory            # don't recall long-term memory
uv run chimera chat --cascade              # tiered routing: weak -> gate -> mid -> gate -> fusion
uv run chimera chat --fuse                 # fusion routing for tool-free turns (read the note)
uv run chimera chat --max-usd 0.50         # ceiling for the WHOLE thread, not for one turn
uv run chimera chat --write-region 'src/**,*.py'   # the only paths the file-writers may touch
uv run chimera chat --model MODEL --workspace DIR --max-steps 8
```

`--workspace`/`-w` enraíza as tools e é de onde `AGENTS.md` é lido; `--max-steps` limita os
passos de chamada de tool dentro de uma mensagem; `--model`/`-m` sobrescreve o slug do modelo
— mas veja a nota sobre roteamento mais abaixo.

Comandos: `/help` · `/new` (thread nova — a atual continua em disco) · `/reset` (o mesmo que
`/new`) · `/model <slug>` (sem argumento volta ao padrão) · `/solve <tarefa>` (entrega ao loop
verificado) · `/exit` (também `/quit`, `/q`).

**Ele é governado, e pergunta a você.** `chat` e `assist` montam a mesma pilha que o caminho
da API monta: um ledger de taint a quem se conta a sua própria mensagem, a cerca
`<<external-data>>` em volta da saída não confiável das tools, o kernel de confiança, a
`--write-region`, o piso de alcance do dono (`CHIMERA_REACH`) e as instruções do dono vindas
de `agent.json`. O aprovador **pergunta no seu terminal** — esta é a única superfície onde há
garantidamente alguém para responder. Medido sobre o corpus de injeção
(`bench/right_hand_governance/RESULTS.md`, 2026-09-08): ataques bloqueados foram de 0 de 7
para 7 de 7, leituras externas devolvidas dentro da cerca de 0 de 12 para 12 de 12, e o
over-block com uma pessoa respondendo é 0,000 — ao custo de cinco perguntas nas oito linhas
legítimas. Por pipe (`chimera chat < script.txt`) não há a quem perguntar, então uma pergunta
vira uma recusa registrada em vez de consentimento silencioso.

Notas de honestidade:

- **Uma chamada de tool recusada é impressa embaixo da resposta** —
  `✗ run_shell did not succeed: …`. O modelo narra em volta das recusas: o caso medido
  respondeu *"The command printed exactly: marker-42"* sobre um comando que o gate de execução
  no host tinha recusado. Uma aprovação também ganha a sua linha
  (`governance: 1 approved this turn`), para que um `y` que você digitou no meio do turno
  deixe rastro depois que a resposta subiu na tela.
- **`--fuse` não funde um turno que leva tools, e um turno de REPL sempre leva.** O roteador
  manda para um modelo único qualquer turno com tools, então na prática um turno com `--fuse`
  aqui é um turno de modelo único. `--cascade` ainda ganha de `--fuse` quando os dois são
  passados. A única rota do terminal que funde de verdade é o `/task` do `assist`.
- **Nomear um modelo o fixa, e a escada de tiers sai da frente.** Sob `--cascade` é a escada
  que escolhe um modelo a cada turno, e ela engolia sem dizer nada o slug que você tinha
  nomeado. Agora `--model` e `/model <slug>` ganham enquanto algum estiver nomeado — uma linha
  avisa que a escada está desligada — e `/model` sem argumento devolve o trabalho a ela.
- Todo turno imprime seus tokens e seu preço — `cost: unavailable` quando o preço de tabela do
  modelo é desconhecido, nunca um zero chutado — e acrescenta uma linha a
  `<home>/usage.jsonl`, que é o que a tela de Custo do app desktop lê.
- **`--max-usd` limita a thread, não o turno.** Um único medidor corre da primeira mensagem
  até o `/exit`; o `/solve` bebe do mesmo dinheiro; e, uma vez gasto, a próxima mensagem é
  recusada antes de ser enviada, em vez de pagar uma chamada para descobrir que não sobrava
  nada. Uma resposta que foi cortada — pelo teto, pelo `--max-steps` ou por um contexto que
  deixou de caber — diz isso na sua própria linha, porque, sem ela, uma resposta truncada se
  lê exatamente como uma resposta terminada.
- **Um turno restaurado se anuncia.** Quando uma thread volta do disco, uma linha discreta
  embaixo da resposta conta os turnos reproduzidos que foram restaurados e quantos deles nunca
  tiveram procedência registrada. Esses são reproduzidos dentro da cerca de dados, e não como
  palavras do próprio modelo, e até agora a cerca era invisível para quem ela protege.
- **`/solve <tarefa>` entrega a conversa ao loop verificado** — planejar, editar, verificar e
  reverter a tentativa quando ela falha, que é o segundo dos dois botões da tela de código do
  desktop. Ele nunca começa sozinho, imprime a tarefa e o teto antes de rodar, e a resposta do
  próprio loop fica registrada na thread. Sem argumento, pega a última coisa que você pediu.
- **Os servidores MCP chegam ao terminal.** Com `CHIMERA_MCP_AUTOLOAD=1` os servidores de
  `mcp.json` são montados antes da cerca, então a denylist, o kernel e o ledger de taint os
  cobrem, e a saída de um servidor chega dentro da cerca de dados como qualquer outra leitura
  externa. Eles são conectados uma vez por processo e compartilhados com o app, então nada é
  iniciado duas vezes.
- `/reset` **começa uma thread nova**; ele não apaga a atual. Ele limpava uma transcrição em
  memória quando nada estava em disco; agora que a thread é um arquivo, limpá-la no lugar
  destruiria trabalho. (Em `assist`, que não persiste nada, `/reset` ainda limpa o contexto.
  `tui` grava neste mesmo armazenamento e redefiniu `/reset` do mesmo jeito.)
- `chimera doctor` diz onde os comandos do agente rodam e se ele pergunta antes: a sandbox
  configurada, se existe mesmo uma sandbox do sistema operacional disponível, e a postura de
  execução no host.

### `assist` — o mesmo braço direito, barato por padrão

`assist` é o `chat` com os padrões de segundo cérebro ligados: a cascata de tiers manda a
conversa fiada para modelos baratos e escala os pedidos difíceis, seu perfil persistente
(`chimera profile`) é o preâmbulo estável, e memória, sugestões e consolidação de fim de
sessão estão ativas. Ao sair ele imprime um recibo de sessão — distribuição de tiers e tokens
medidos — para que "barato por padrão" seja um número.

```bash
uv run chimera assist                      # cascade, profile and memory on
uv run chimera assist --no-cascade         # one default model instead of the ladder
uv run chimera assist --no-memory          # don't recall long-term memory
uv run chimera assist --max-usd 0.25       # ceiling for the WHOLE run, not for one turn
uv run chimera assist --write-region 'src/**'      # the only paths the file-writers may touch
uv run chimera assist --model MODEL --workspace DIR --max-steps 8
```

Comandos: `/help` · `/task <pedido difícil>` (fusão a plena potência, um tiro só) ·
`/solve <tarefa>` (entrega ao loop verificado) · `/profile <tipo>: <fato>` (lembrar algo sobre
você — tipos: `preference`, `project`, `context`, `name`) · `/model <slug>` · `/reset` (limpa
o contexto da conversa; nada é apagado) · `/exit` (também `/quit`, `/q`).

Governado exatamente como o `chat` — o mesmo registry, o mesmo aprovador que pergunta, as
mesmas linhas de recusa, governança e custo, a mesma linha em `usage.jsonl`, os mesmos
servidores MCP, o mesmo medidor de `--max-usd` sobre a execução inteira e o mesmo `/solve`.
Duas diferenças que vale conhecer: **`assist` não guarda thread** (esquece ao sair — use
`chat` para uma conversa que você queira de volta); e nomear um modelo o fixa, o que desliga
a escada de tiers enquanto ele estiver fixado — é a escada que escolhe o modelo, e ela
aceitava o slug e o ignorava.

O `/task` roda uma fusão forçada só sobre o pedido: a conversa não é mandada ao painel, de
propósito, porque alimentar um painel mais um juiz mais um sintetizador com a thread
multiplica o custo da rota que existe para ser usada com parcimônia. A resposta dele *faz*
parte do contexto do turno seguinte agora, e ela imprime um preço e escreve uma linha em
`usage.jsonl` como todo turno — a rota mais cara do terminal era justamente a que a tela de
Custo não conseguia ver.

### `tui` — app de terminal em tela cheia

Uma UI Textual em tela cheia sobre o mesmo núcleo conversacional. Dois painéis: um **log de
conversa** que renderiza respostas como Markdown (código com crase é destacado por sintaxe),
com os tokens do modelo **transmitidos ao vivo** assim que chegam; e um **painel de
atividade** mostrando o que o agente fez naquele turno — as tools que chamou, a contagem de
tokens e o custo, e quantos fatos de memória foram lembrados.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
uv run chimera tui --model MODEL --workspace DIR --max-steps 8
uv run chimera tui --max-usd 2.00     # a ceiling for the whole session, shown in the panel
uv run chimera tui -s standup         # resume a named thread (--new starts a fresh one)
uv run chimera tui --write-region 'src/**'  # the file-writers may touch nothing else
```

Não são as mesmas flags dos REPLs. `tui` tem `--stream`/`--no-stream`, que eles não têm, e
`--max-usd`, que para a sessão assim que ela gastou aquilo. Essa flag esperava um lugar onde
aparecer: o painel de atividade agora leva uma linha `budget` com o que sobrou, porque um teto
que ninguém vê transforma um turno que parou por dinheiro num turno que parou sem motivo
visível.

`chimera tui --session standup` retoma uma thread com nome, `--new` começa uma nova no lugar, e
`--write-region` estreita o que os escritores de arquivo podem tocar. As três significam aqui
exatamente o que significam uma seção acima, no mesmo armazenamento de sessões. Só o `--cascade`
do `chimera chat` não tem equivalente aqui.

Comandos: `/model <slug>` · `/new` (thread nova; `/reset` é um apelido) · `/clear` (limpa a tela) ·
`/stream` (alterna tokens ao vivo) · `/help` · `/exit` (também `/quit`, `/q`). Teclas:
`Ctrl+R` thread nova · `Ctrl+L` limpar · `Ctrl+P` paleta de comandos · `PgUp`/`PgDn` rolar ·
`Ctrl+C` sair. Os comandos de barra se autocompletam enquanto você digita.

Notas de honestidade:

- **Ela é governada, e as perguntas dela são desenhadas em vez de digitadas.** A mesma pilha
  que os REPLs montam: um ledger de taint a quem se conta a sua própria mensagem, a cerca
  `<<external-data>>` em volta da saída não confiável das tools, o kernel de confiança, o piso
  de alcance do dono e os servidores MCP conectados. O que manteve esta superfície de fora até
  2026-09-09 não foi a pilha, foi a pergunta — o Textual é dono do terminal, então uma
  pergunta escrita no stdin é escrita onde ninguém consegue olhar. Medido num pty: um turno
  assim travou 123,8 s contra um timeout de 120 s e voltou como `✗ run_shell` sem explicação
  (`bench/right_hand_governance/RESULTS.md`, Parte 2). **Os dois** gates agora abrem um modal:
  a confirmação de execução no host e o aprovador de governança. `y` ou `n`, Escape recusa, o
  botão Não fica com o foco para que o Enter não aprove por acidente, e uma contagem
  regressiva diz quanto o silêncio ainda tem — o silêncio continua recusando, e agora avisa.
  No mesmo corpus e no mesmo instrumento, os ataques bloqueados foram de 0 de 7 para 7 de 7 e
  as leituras externas devolvidas dentro da cerca de 0 de 15 para 12 de 15 (as três que ficam
  de fora são o seu próprio repositório, que não é externo).
- **Uma chamada recusada agora diz por quê, embaixo da resposta.** O painel de atividade já
  mostrava a frase da própria tool debaixo do `✗` dela; o log de conversa mostra também
  `✗ run_shell did not succeed: …`, que é onde está igualmente a narrativa do modelo sobre um
  comando que nunca rodou. Uma aprovação também ganha uma linha
  (`governance: 1 approved this turn`), então um `y` que você clicou no meio do turno deixa
  rastro.
- **A conversa sobrevive à janela.** Cada turno é salvo em `<home>/sessions` — o mesmo
  armazenamento que `chat` grava e que `chimera sessions` lista, então uma thread começada em
  uma superfície continua na outra — e a thread mais recente é retomada por padrão. É por isso
  que `/reset` começa uma thread NOVA em vez de limpar esta: limpar no lugar uma thread que
  agora é um arquivo seria o comando que a destrói. O histórico na tela não é redesenhado ao
  retomar, e a linha abaixo do banner diz quantos turnos o modelo enxerga que a tela não
  mostra. `docs/commands.md` tem o resto.
- A transmissão de tokens só existe no caminho de modelo único — sob `--fuse` (um turno
  painel→juiz→sintetizador) não há tokens incrementais, então o painel mostra um status
  "sintetizando" em vez de um cursor falso. Esse rótulo segue a flag e não a rota: como no
  `chat`, um turno que leva tools não funde, e um turno de REPL sempre leva.
- O custo aparece como "indisponível" quando o preço de tabela do modelo é desconhecido (nunca
  é chutado), e cada turno é acrescentado a `<home>/usage.jsonl` como nos REPLs.
- Não há verificar/reverter aqui e não há `/solve`: os dois foram para o `chat` e o `assist`.
  Os servidores MCP estão montados, o que antes não acontecia, exatamente pelo motivo por que
  eram retidos — saída de MCP é conteúdo não confiável por definição, e agora há onde
  cercá-la. Verificar-ou-reverter também roda em `chimera solve` e `chimera project`.
- Se o Textual não estiver instalado, `tui` cai de volta para o REPL `chat` comum, passando
  cada argumento explicitamente para que essa queda sobreviva ao primeiro turno. Transmissão
  não significa nada lá, e a thread é salva como qualquer outra thread de `chat`.

### `serve` — gateway de mensageria (HTTP ou Discord)

Expõe o agente com uma conversa (e sua memória) **por chat**. O núcleo de roteamento é
agnóstico de transporte; adaptadores se plugam nele.

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Cada `chat_id` mantém seu próprio contexto, então usuários/threads diferentes não se
misturam.

**Operação desatendida (webhooks).** Registre um job que dispara em um POST HTTP de
entrada, para que o Chimera rode sem ninguém digitando — um push do GitHub, um evento do
Stripe, um ping de cron-as-a-service:

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

O corpo do POST é entregue à tarefa do job como contexto, e todo job registrado para
aquele hook roda. `GET /health` e `POST /chat` continuam funcionando ao lado dele.

**Discord nativo.** Rode o Chimera como um bot do Discord — cada canal é uma sessão, e o
agente também pode enviar mensagens via a tool `send_message`:

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

Crie o bot em <https://discord.com/developers>, habilite a intent **Message Content**,
e convide-o para seu servidor. Ele responde em qualquer canal que consiga ver (filtrado
para ignorar as próprias mensagens e as de outros bots). O token é lido do ambiente —
nunca fixado no código.

**Telegram nativo.** Mesmo padrão de adaptador, e não precisa de **nenhuma dependência
extra** (a Telegram Bot API é HTTP puro):

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**Slack nativo.** Recebe via Socket Mode (precisa do extra `messaging`) e envia via a
Web API. Habilite o Socket Mode no seu app Slack para obter um token de nível de app:

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp (enviar).** O WhatsApp é *baseado em push* (as mensagens chegam em um webhook
Meta que você hospeda), então, diferente dos outros, não há conexão para abrir. Defina as
credenciais da Cloud API e o agente pode **enviar** mensagens WhatsApp via a tool
`send_message` em qualquer modo de `serve`:

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**WhatsApp bidirecional.** Aponte o webhook do seu app Meta para
`https://<your-host>/whatsapp` e defina `CHIMERA_WHATSAPP_VERIFY_TOKEN` (qualquer string
que você escolher, correspondendo à config do app). O `chimera serve` então verifica a
inscrição (`GET /whatsapp`) e roteia mensagens de entrada (`POST /whatsapp`) através do
gateway, respondendo pela Cloud API. O WhatsApp ainda precisa de uma URL pública para o
webhook — essa é a única parte fora do Chimera.

**Signal nativo (bidirecional).** O Signal não tem API oficial, então o Chimera fala com
uma ponte [`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api) que
você roda (Docker) e vincula ao seu número — HTTP puro, sem dependência Python:

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — Tier-1, completion de uma tacada só

Uma única chamada de modelo, sem tools, sem fusão. O caminho mais barato.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**Visão / colar imagem.** Anexe imagens com `--image` (um caminho ou URL, repetível)
— precisa de um modelo com capacidade de visão:

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Modo Entregável (produz um artefato)

Enquanto `run`/`chat` respondem de forma conversacional, `deliver` produz um documento
completo e autocontido (relatório, plano, spec, README...) e o escreve em um arquivo.

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — o loop bruto de tool-calling ReAct

Pensamento → Ação (tool) → Observação, até uma resposta final. As tools ficam restritas
ao workspace.

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion (o diferencial)

Roda um *painel* de modelos, um *juiz* analisa as respostas deles
(consenso / contradições / pontos cegos), e um *sintetizador* escreve a resposta final.
Use `--show-panel` para ver o trace completo.

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

A fusão custa ~2-3× uma chamada única, então reserve-a para raciocínio difícil. O `fuse`
também imprime o custo em tokens por estágio (painel / juiz / síntese) para que você veja
para onde os tokens de uma execução de fato vão.

**Fusão seletiva (LIGADA por padrão, economiza tokens).** O motor sonda os primeiros
`CHIMERA_FUSION_PROBE_K` modelos do painel (padrão 2) e, quando as respostas deles
concordam de perto, pula o resto do painel *e* o juiz — sintetizando direto a partir das
respostas concordantes. A checagem de concordância é uma comparação de texto local barata
(sem chamada extra de modelo), então um turno *discordante* escala para o pipeline
completo e custa exatamente o mesmo que a fusão completa, enquanto um turno *concordante*
sai mais barato. Ajuste o limiar com `CHIMERA_FUSION_AGREEMENT` (0–1, padrão 0.8), ou
defina `CHIMERA_FUSION_MODE=full` (ou passe `--full`) para sempre rodar o painel + juiz
completos.

Por que é o padrão: em 3 execuções de `chimera fusion-bench --tasks hard` (um painel pago
de 3 modelos), isso cortou tokens em **~20–28%** e acertou em **todo** turno em que de fato
interrompeu antecipadamente (16/16). A acurácia geral oscilou de 0 a −8,3pp entre
execuções, mas essa variância cai inteiramente no balde *escalado* — onde o modo seletivo
roda o pipeline idêntico ao completo — então é não-determinismo do modelo, não um custo do
early-stopping. Rode o bench na sua própria carga de trabalho para ver o trade-off para o
seu painel e suas tarefas:

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **Escolha modelos de painel confiáveis.** A fusão só compensa se todo membro do painel
> de fato responde. Evite slugs de modelo `:free` do OpenRouter em `CHIMERA_FUSION_PANEL`
> — eles têm limite de taxa (HTTP 429) sob carga real, e o painel silenciosamente encolhe
> para o que quer que sobre de modelo pago. Um trio barato e confiável:
> `openrouter/deepseek/deepseek-chat`, `openrouter/openai/gpt-4o-mini`,
> `openrouter/meta-llama/llama-3.3-70b-instruct`.

### Skill cards (cartões de raciocínio TRS, experimental)

O agente destila o que aprende em **cartões de raciocínio** — os cinco campos
Trigger / Do / Avoid / Check / Risk (mais palavras-chave de recuperação) — tanto de
sucessos (um cartão de *padrão*) quanto de falhas recorrentes (um cartão consultivo de
*anti-padrão*). Quando `CHIMERA_SKILL_CARDS=on`, `solve` recupera os top-k cartões
relevantes (BM25 sobre nome + descrição + gatilhos) e os injeta no contexto de raciocínio
do trabalhador, então o agente reaproveita o que funcionou e evita modos de falha
conhecidos. Isso fecha o loop — antes, as skills aprendidas eram armazenadas e nunca lidas
de volta.

Desligado por padrão: injetar cartões adiciona tokens de prompt, e a economia de *tokens*
do TRS vem de encurtar traces de raciocínio longos, então em tarefas de resposta curta o
ganho é acurácia, não custo. Isso não é hipotético — na suíte de resposta curta `hard`
(deepseek-v3.1 pago), o `skillcard-bench` mediu cartões custando **+290% de tokens** e
**−8pp de acurácia** contra não usar cartões: com um modelo perto do teto e sem um trace
longo para encurtar, cartões genéricos são puro overhead que pode distrair. Habilite os
cartões para cargas de trabalho de **raciocínio longo** (matemática/código com traces
extensos) onde a matemática de tokens se inverte, e sempre meça seu próprio trade-off
primeiro com uma checagem de ground-truth:

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

O bench reporta a acurácia com vs. sem cartões, o delta de tokens, a taxa de acerto do
cartão, e a acurácia dividida por acerto/erro, com um veredito PASS quando a acurácia com
cartões fica dentro de 1pp da baseline sem cartões.

### Schemas de tool compactos (experimental)

Schemas de tool — especialmente os importados de servidores MCP ou specs OpenAPI —
carregam ruído de anotação (exemplos, títulos, padrões, prosa de parâmetro em várias
frases, corpos de requisição aninhados) que é reenviado ao modelo em **todo** passo ReAct.
Com `CHIMERA_COMPACT_SCHEMAS=on`, esse ruído é removido e as descrições de parâmetro são
cortadas no momento do anúncio, **sem** tocar em nada que afete uma chamada (o nome e a
descrição da função, e o `type` / `properties` / `required` / `enum` de todo schema são
preservados). Os schemas canônicos ficam intactos — só a cópia enviada ao modelo encolhe.

A economia é maior em conjuntos de tools MCP/OpenAPI verbosos e se acumula a cada passo;
as tools nativas já são enxutas, então sua redução é pequena. Meça seu próprio conjunto de
tools primeiro (sem chamadas de modelo — só conta tokens):

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

Desligado por padrão. Como a compactação só remove ruído de anotação (nunca a estrutura),
o único risco é o modelo ter um pouco menos de prosa para escolher uma tool — então ela se
mantém conservadora, e você deveria confirmar o comportamento de chamada de tool na sua
carga de trabalho antes de habilitar.

### `solve` — autônomo Tier-2 (plano + verificar-ou-reverter)

Planeja a tarefa, executa com o loop do agente, depois **verifica com um comando
executável**. Se a verificação falhar, reverte o workspace e tenta de novo com feedback.
O verificador (código de saída 0 = sucesso) é a verdade fundamental.

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

Flags úteis:

| Flag | Significado |
|------|---------|
| `--verify "<cmd>"` | comando que precisa sair com 0 (testes, um build, um linter) |
| `--workspace`, `-w` | onde o agente lê/escreve (padrão `.`) |
| `--max-attempts N` | orçamento de verificar-ou-reverter (padrão 3) |
| `--max-steps N` | passos de tool-calling por tentativa (padrão 8) |
| `--fuse` | produz o **plano** via fusão (raciocínio profundo) |
| `--guard` | controla toda chamada de tool através do kernel de governança |
| `--no-plan` / `--no-manager` | pula o estágio de planejamento / review |
| `--rubric` | o Manager julga via a **rubrica em cascata** (seguir instrução → factualidade → racionalidade) |
| `--no-remember` | não escreve automaticamente um fato de memória no sucesso |
| `--no-evolve-skills` | não propõe automaticamente uma skill aprendida quando uma tarefa se repete |
| `--isolate` | roda em um git worktree descartável; arquivos alterados são copiados de volta só no sucesso |
| `--require-diff` | uma tentativa que não mudou **nenhum arquivo** falha e é retentada — para uma tarefa de código, uma explicação não é uma correção |
| `--keep-workspace` | na falha, deixa as edições da última tentativa em disco em vez de reverter — para quando um avaliador **externo** decide passa/falha |
| `--diff-feedback` | mostra a uma tentativa falha seu próprio diff revertido, enquadrado como um caminho a não retomar |
| `--stagnation-fuzzy` | casa assinaturas de falha repetida de forma aproximada, para que o pivô anti-estagnação dispare em falhas de mesma causa cuja redação difere |

> **Sobre `--max-steps`.** O padrão de 8 é ajustado para workspaces pequenos. Em um
> **repositório grande, ele é a restrição vinculante**, não o modelo: a execução 1 do
> SWE-bench marcou um 0,0pp exato com 8 passos contra um checkout de 250 MB, e a mesma
> configuração com **30 passos** elevou a taxa de patch da baseline de 47% para 74%
> ([`bench/swe_bench/RESULTS.md`](../../../bench/swe_bench/RESULTS.md)). Se o agente explora e
> depois termina sem editar, aumente isto primeiro.

> **`--require-diff` e `--keep-workspace` são para avaliação externa.** O `solve` é
> verificar-ou-reverter: quando *ele* é dono da decisão de passa/falha, reverter uma
> tentativa falha é correto. Quando outra coisa é dona dela — um job de CI, um harness de
> benchmark, um humano revisando o diff — `--keep-workspace` impede que o trabalho do
> agente seja desfeito antes que esse avaliador o veja, e `--require-diff` impede que uma
> explicação confiante seja pontuada como uma mudança concluída. Ambos ficam **desligados
> por padrão**.

**O `solve` aprende entre execuções.** Cada execução alimenta um loop comportamental
fechado, todo controlado por verificar-ou-reverter para que só o trabalho verificado tenha
algum efeito: (1) **lições** relevantes de tentativas passadas (falhas são favorecidas) são
incorporadas ao plano/prompt, e o **primeiro passo defeituoso** de uma tentativa falha é
localizado e alimentado na nova tentativa; (2) em um sucesso verificado, um fato de
**memória** deduplicado é escrito (lembrado depois por `chat`/`crew`); e (3) quando um
padrão de tarefa se repete (≥ 2 sucessos anteriores), uma **skill** reutilizável é proposta
— através do painel de fusão e mantida por **transferibilidade** entre modelos quando
`--fuse` está ligado — e só é mantida se passar na validação de governança e em um smoke
test executável.

### `crew` — multi-agente Tier-3

Um time de agentes com papéis colabora em uma tarefa e um supervisor sintetiza a resposta
final.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — crew de SDLC (planejar → construir → testar → revisar)

Um pipeline de ciclo de vida de software pré-montado com **verificar-ou-reverter** no
estágio de teste: `plan` decompõe a tarefa, `build` a implementa, `test` roda o
verificador (revertendo e retentando o build em caso de falha), e um revisor critica o
resultado.

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

Cada estágio imprime com um ✓/✗; a execução é `success` só se o verificador do estágio de
teste passou.

### `meta` — agentes construindo agentes

Projeta o blueprint de um agente especializado (nome, tools, prompt de papel) para uma
tarefa.

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — veredito de governança

Mostra a decisão do kernel de confiança (allow / warn / review / block) para uma ação.

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — benchmark de evolução contínua

Mede se a performance *se sustenta* ao longo de uma cadeia de tarefas (a prova
anti-degradação): taxa geral de aprovação, primeira metade vs. segunda metade, maior
sequência.

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

O relatório também carrega uma flag de degradação **estatisticamente honesta**: em vez de
confiar em uma simples subtração primeira-menos-segunda-metade (em uma cadeia curta uma
oscilação de 0,2 costuma ser ruído), `degraded_significant` só é `1.0` quando um intervalo
de confiança de Wilson sobre a queda exclui zero, `-1.0` quando a amostra é pequena demais
para dizer, e `0.0` caso contrário — mais os limites `degradation_ci_low/high`.
Separadamente, `CHIMERA_SKILL_ACCEPT_MODE=wilson` condiciona a decisão de aceitar uma
skill entre modelos ao limite de confiança *inferior* da taxa de transferência (então um
2-de-3 sortudo deixa de contar); o padrão `point` mantém a taxa bruta, já que o limite de
Wilson é rigoroso demais em painéis minúsculos.

### `sandbox-bench` — avaliação de estado + efeito colateral

Os benches de texto avaliam a *resposta* do modelo; este avalia o que o agente **fez**.
Cada tarefa roda em um diretório de sandbox isolado, e o harness compara o estado final
dos arquivos contra o objetivo (qualquer caminho permitido, estilo resultado) **e**
separadamente conta *efeitos colaterais nocivos* — mutações fora do conjunto permitido
declarado para a tarefa. Assim, um agente que produz o resultado certo enquanto destrói um
arquivo não relacionado é pego, não pontuado como uma aprovação limpa.

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

Reporta `pass_rate` e `side_effect_rate`. Ele traz a *metodologia* (uma `StatefulTask` com
`goal_check` + conjunto `allowed` de mutação), não uma grande suíte de tarefas — autore
tarefas para suas próprias tools. Os avaliadores de texto existentes continuam corretos
para trabalho puramente de perguntas e respostas.

### `memory` — memória de longo prazo curada

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

A recuperação passa por um **gate de admissão** (uma fronteira de confiança): uma memória
recuperada só entra no prompt se for relevante *e* livre de texto de override/injection
(defesa contra jailbreak baseado em memória). `memory prune` esquece sob um orçamento por
um modelo de **valor** multifator (recência, especificidade, tipo, curadoria,
confiabilidade) — não um único critério.

A **camada de grafo** extrai triplas `(fonte, relação, alvo)` das suas memórias
(`PassaPro uses Supabase`, `Alex prefers TypeScript`), então fatos podem ser recuperados
por entidade, não só por palavra-chave.

### `cron` — jobs agendados & SOPs de evento

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — quadro de tarefas com raias de trabalhador

Um quadro (`backlog → doing → review → done`) onde cada card nomeia uma *raia* que o
despacha para a pilha do agente: `solve` (autônomo Tier-2, verificar-ou-reverter) ou
`crew` (pipeline de papéis Tier-3). A visão operacional do loop que o agente já roda.

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` percorre cada card backlog → doing → done (sucesso) ou → review (precisa de
atenção). `learn` reaproveita o detector de recorrência do cron-learner para enfileirar
tarefas que o agente repete (deduplicadas contra o quadro) — agende-o para preencher o
backlog automaticamente.

### `workflow` — loops projetados (Loop Engineering)

Autore um loop autônomo como YAML em vez de um prompt improvisado. Cada passo `uses` uma
capacidade (`run` / `shell` / `solve` / `crew` / `lifecycle`), pode ser condicionado ao
passo anterior (`when: prev_succeeded | prev_failed`), e pode repetir (`repeat`, `until:
success`).

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

### `drift` — gate de desvio spec↔código

Mantém uma spec e o código alinhados. Uma spec é um pequeno YAML de requisitos (`defines`
um símbolo / `contains` uma regex / `absent` uma regex / `command` sai com 0). O gate sai
com código diferente de zero em caso de desvio, então serve também como um verificador.

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — importar de outro agente

Traz **config + skills** do Hermes ou OpenClaw, e com `--apply` também **faz merge da
memória de longo prazo** (deduplicada, não-destrutiva). O padrão é uma prévia em modo
dry-run.

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

O merge de memória reporta contagens `{ADD, UPDATE, NOOP}` — duplicatas viram `NOOP`,
então rodar de novo é seguro.

### `evolve` — evolução de modelo opt-in (avançado)

`chimera solve --collect` (ligado por padrão) registra cada execução como uma trajetória.
Os comandos `evolve` transformam isso em datasets prontos para treino e uma recipe LoRA
executável. **O treino é externo e opt-in** — ele muda os pesos do modelo, então nunca
acontece automaticamente; o Chimera prepara os dados e um script e para.

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` aceita ajustes de recipe: `--min-steps N` mantém só traces de longo horizonte,
`--diverse` mantém no máximo um exemplo por tarefa (a diversidade de tarefas é o gargalo
de curadoria), e `--min-process P` (SkillCoach) mantém só traces cujo score de
*seguimento de passo* ≥ P — a fração de passos de tool que produziu um resultado
bem-sucedido e visível — para que um sucesso sortudo que se debateu por chamadas de tool
falhas não entre no treino. Os eventos por passo por trás desse score são capturados
automaticamente em toda execução de `solve`; o filtro fica desligado por padrão
(`CHIMERA_SFT_MIN_PROCESS` define um padrão global). O `evolve tune` é diferente de
treinar — ele roda uma **meta-busca** sobre a *spec* do agente (modelo, prompt de sistema,
orçamento de passos, painel, profundidade de memória), pontuando cada candidato nos
cenários diários e só mantendo uma edição em caso de **não-regressão**. Ele chama modelos
mas nunca muda pesos, então é seguro de rodar a qualquer momento.

Depois, para de fato treinar, em uma GPU (ou Colab): `pip install chimera-agent[train]`
(ou o `requirements.txt` da recipe) e `python recipe/train.py`. Aponte
`CHIMERA_DEFAULT_MODEL` para o modelo base + adapter ao servir.

### `pet` — um bichinho de estimação virtual

Um pequeno companheiro persistente cujas estatísticas mudam enquanto você está fora.
Não precisa de chave.

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## Dicas

- **Tools vs. raciocínio.** Turnos de tool-calling sempre usam um único modelo (a fusão
  não consegue chamar tools); a fusão fica reservada para raciocínio profundo sem tool.
- **Inspecione o que aconteceu.** `CHIMERA_LOG_LEVEL=DEBUG` mostra logs de roteamento e
  de acionamento de fusão.
- **Mantenha os testes honestos.** Um bom comando `--verify` (uma suíte de testes de
  verdade) torna o `solve` confiável — é a verdade fundamental executável à qual o agente
  é submetido.
