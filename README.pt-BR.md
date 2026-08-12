<div align="center">

<img src="assets/logo-wide.png" alt="Chimera logo" width="460" />

# Chimera

**O agente auto-evolutivo governado — provado e governado.**<br/>
<sub>Pensa com muitas mentes, faz o trabalho de verdade sozinho, aprende só o que é comprovado, e é seguro por arquitetura.</sub>

[![Website](https://img.shields.io/badge/chimeraagent.space-visit-3b82f6.svg)](https://chimeraagent.space)
[![PyPI](https://img.shields.io/pypi/v/chimera-agent.svg?color=blue&label=PyPI)](https://pypi.org/project/chimera-agent/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11-3.13](https://img.shields.io/badge/python-3.11--3.13-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
[![Reddit](https://img.shields.io/badge/Reddit-r%2FChimeraAgent-FF4500.svg?logo=reddit&logoColor=white)](https://www.reddit.com/r/ChimeraAgent/)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![Donate](https://img.shields.io/badge/Donate-Stripe-635BFF.svg?logo=stripe&logoColor=white)](https://buy.stripe.com/9B6aEQ57q91m1Gp7Lz77O01)

<sub><a href="README.md">English</a> · <b>Português</b> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.it.md">Italiano</a> · <a href="README.pl.md">Polski</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

A maioria dos assistentes de IA aposta tudo em um **único** modelo e esquece tudo quando a conversa
termina. **O Chimera faz duas coisas de forma diferente:** para perguntas difíceis, ele consulta
**vários** modelos de IA ao mesmo tempo e combina as respostas em um resultado único e mais forte,
e ele **lembra e aprende**, ficando mais útil quanto mais você o usa. Ele não apenas conversa — dê
um objetivo a ele e ele planeja, usa ferramentas, confere o próprio trabalho e mantém só o que
realmente funciona.

> **Gratuito e open-source (Apache-2.0), em desenvolvimento inicial mas ativo.** Ele já funciona de
> ponta a ponta: converse com ele, deixe que conclua tarefas sozinho, rode-o como um bot no seu app
> de mensagens favorito, publique-o em um servidor para que trabalhe 24/7 e veja-o aprender com o
> que faz. É **alpha** — sólido e bastante testado (**mais de 2.000 testes automatizados**, checagem de
> tipos e lint rigorosos em cada mudança), mas ainda não endurecido em produção pesada.

---

## Por que o Chimera

Pense na maioria das ferramentas de IA como perguntar a **um** especialista e torcer para que ele
esteja certo. O Chimera é como ter um **painel de especialistas** que debatem, um **juiz justo** que
pondera as respostas deles e um **redator** que entrega o melhor resultado combinado — e, além disso,
um colega de equipe que de fato **faz o trabalho** e **aprende** com ele. Veja o que o torna especial,
em termos simples:

- 🧠 **Muitas mentes, uma resposta.** Para perguntas difíceis, o Chimera faz a mesma pergunta a vários modelos, deixa um modelo comparar as respostas e faz um modelo final escrever a melhor resposta combinada — assim você recebe algo mais equilibrado e com menos chance de estar errado do que qualquer modelo sozinho. (Ele só faz isso quando vale a pena, para se manter rápido e barato.)
- 🚀 **Ele faz o trabalho, não só conversa.** Dê um objetivo. Ele o divide em partes, usa ferramentas, edita arquivos, roda os testes e **só mantém a mudança se ela passar**. Se algo quebra, ele desfaz e tenta de novo — então não deixa bagunça para trás.
- 🧬 **Ele melhora quanto mais você o usa.** Ele lembra suas preferências e fatos importantes entre conversas e, silenciosamente, transforma tarefas que se repetem em skills reutilizáveis. Foi feito para continuar melhorando em vez de piorar aos poucos ao longo do tempo — um problema que degrada muitos agentes sem que se perceba.
- 🛡️ **Seguro por design.** Toda ação arriscada passa antes por uma checagem de segurança, qualquer coisa destrutiva pede confirmação, e código não confiável pode rodar dentro de um container isolado, com a rede desligada. (Essas checagens são um primeiro filtro barato, não a fronteira de verdade — o sandbox é; e o isolamento em container é opt-in. Veja [SECURITY.md](SECURITY.md).)
- 🔌 **Qualquer modelo, roda em qualquer lugar.** Use grandes modelos hospedados ou os seus próprios modelos locais por uma única interface — no seu notebook ou em um servidor de US$ 5, o tempo todo.
- 🧩 **Realmente seu.** Open-source, sem lock-in, sem precisar de conta de fornecedor. Você roda, você é dono, você pode mudar qualquer coisa.

## Como o Chimera se compara

O Chimera não tenta ganhar em *quantidade de canais* dos gigantescos projetos de agentes. Ele aposta
nas três coisas que um verdadeiro estudo de engenharia reversa de cinco líderes (OpenClaw, Hermes,
nanobot, CrewAI, LangGraph) descobriu que **todos deixam em aberto** — e faz delas o seu núcleo:

- 🧬 **Auto-evolução com um sinal de aptidão.** Os outros "aprendem" acrescentando o que quer que tenha acontecido, ou por pull requests humanos — nada mede se uma mudança aprendida realmente ajudou. O Chimera mantém uma mudança **só quando um resultado verificado prova que ela ajudou**: o passo de evolução é condicionado ao diff real da árvore de trabalho e a um A/B honesto, nunca à palavra do modelo. Evidência independente de que isso importa: o [EvoAgentBench (arXiv 2607.05202)](https://arxiv.org/abs/2607.05202) mediu que métodos *automáticos* e não condicionados de codificação de experiência produzem rotineiramente **transferência negativa** — um método popular regrediu **−12,3 pontos** em tarefas para as quais não foi ajustado. O gate do Chimera agora também roda um **holdout de transferência**: uma mudança aprendida não pode regredir uma fatia disjunta de mesma capacidade antes de ser promovida, para que ele não possa apenas decorar a própria avaliação.
- 🛡️ **Segurança por arquitetura.** Prompt injection é hoje amplamente considerado *impossível de corrigir*; os agentes populares mitigam na camada da aplicação ou declaram o tema fora de escopo (um deles lançou 135 mil instâncias expostas publicamente e um marketplace ~12% cheio de skills maliciosas). O Chimera traz uma camada de defesa real — **opt-in via `--taint`, desligada por padrão**: rastreia a proveniência da contaminação de forma *heurística* (fluxo de referência/conteúdo literal, **não** dataflow real — um modelo que parafraseia o texto contaminado o "lava"), remove tokens de controle de conteúdo não confiável, restringe o acesso a ferramentas perigosas no restante de uma execução contaminada e protege retentativas com efeitos colaterais; código não confiável roda num container isolado, opt-in. No corpus embutido de **7 ataques**, **6 de 7** chamadas nocivas são bloqueadas (**~14%** ainda passam) — medido sobre um agente **já injetado** que tenta a chamada do atacante, sem modelo no circuito. O braço sem defesa é 100% por construção, não por medição: uma ferramenta sem wrapper sempre executa, então trate isso como o piso definicional contra o qual a camada é comparada, não como um sistema de referência. Isso nada diz sobre quão fácil é injetar o modelo — a metade mais difícil, ainda em aberto ([`chimera/eval/injection.py`](chimera/eval/injection.py)). O [`SECURITY.md`](SECURITY.md) diz claramente o que ainda passa (handoff entre sub-agentes, fusão/sumarização, pontos de entrada fora da CLI) — a fronteira de contenção é o sandbox; esta camada é defesa em profundidade sobre ele.
- 📊 **Benchmarks honestos e publicados.** ~20% dos casos "resolvidos" de um leaderboard popular estão, na verdade, errados. O Chimera reporta cada número com um intervalo de confiança — **incluindo as execuções em que ele não venceu** — e nunca refaz as rodadas em busca de significância. Uma execução pareada registrada mostra o loop completo **elevando um modelo fraco numa suíte pré-registrada de 100 tarefas — 48% → 71% (+23pp), IC 95% [+12,6%, +28,6%] — estatisticamente significativa** (o IC exclui o zero), a partir de **28 tarefas que ele recuperou** (falha crua → aprovação verificada) contra 5 regressões. Uma corrida, sem re-roll. Isto **substitui uma corrida anterior da mesma suíte** (9% → 15%, +6pp), cujo harness avaliava com um arquivo de teste que o agente podia editar — e que, na re-corrida, ele editou uma vez. A direção e a significância se replicaram e ficaram mais fortes com o grading endurecido; a errata, a evidência de adulteração preservada e o porquê das taxas absolutas da corrida original serem tão mais baixas estão em [`bench/local_lift/RESULTS.md`](bench/local_lift/RESULTS.md). E no **Terminal-Bench oficial**, um A/B pré-registrado com N=40 chegou a um **piso dominado por variância, sem diferença significativa em nenhuma direção** — publicado como está ([`bench/terminal_bench/RESULTS.md`](bench/terminal_bench/RESULTS.md)), incluindo a **retratação de uma leitura intermediária errada** assim que o braço de controle foi medido. Resultados nulos e autocorreções também são lançados; esse é justamente o ponto.

**Em uma linha: o agente auto-evolutivo governado — provado e governado.** É alpha, e diz isso.

## Benchmarks (honestos)

Dois números registrados, ambos verdadeiros, publicados juntos de propósito — um agora significativo,
outro humilhante. (Também aparecem na tela **Maturidade & Benchmarks** do app de desktop, direto do
snapshot embarcado.)

- **Elevação de modelo fraco (significativa).** Um modelo barato (`mistral-small-3.2-24b`) + o loop de
  retentativa do Chimera contra o mesmo modelo sozinho, numa **suíte pré-registrada de n=100** (desenho
  e tarefas commitados e publicados antes de qualquer chamada de modelo): **48,0% → 71,0% (+23,0pp)**,
  IC 95% pareado **[+12,6%, +28,6%] — estatisticamente significativo** (o IC exclui 0), a partir de
  **28 tarefas que o loop recuperou** (falha crua → aprovação verificada) contra 5 regressões. Um
  modelo, uma semente/tarefa, tarefas Python pequenas e autocontidas — **NÃO** é SWE-bench e não
  generaliza para repositórios reais. Uma corrida, sem re-roll.
  **Isto substitui uma corrida anterior da mesma suíte** (9,0% → 15,0%, +6,0pp) cujo harness avaliava
  com um arquivo de teste que o agente sob teste podia editar. Refazer com o teste original restaurado
  pegou o agente reescrevendo o próprio teste de avaliação em uma tarefa — ou seja, o buraco era real —
  e a elevação se replicou *maior*, não menor. A afirmação da corrida anterior de que "85 das 100
  tarefas são difíceis o bastante para reprovar os dois braços" também não se sustentou: a re-corrida
  mede 24. A errata completa, a evidência de adulteração preservada e o que não pôde ser re-verificado
  estão em [`bench/local_lift/RESULTS.md`](bench/local_lift/RESULTS.md).
  Fonte: [`bench/local_lift/_reverify_n100/paired.json`](bench/local_lift/_reverify_n100/paired.json), [`PREREGISTRATION.md`](bench/local_lift/PREREGISTRATION.md).
- **SWE-bench Verified — a evidência externa mais forte, e ela sobreviveu a uma replicação desenhada
  para matá-la.** Três corridas pré-registradas em fatias do `django/django`, avaliadas **somente** pelo
  harness oficial `swebench` 4.1.0 em Docker — nunca auto-reportadas.

  | corrida | fatia | baseline | + Chimera | Δ pareado | IC 95% | |
  |---|---|---|---|---|---|---|
  | 1 (`max_steps=8`) | 19 | 36,8% | 36,8% | +0,0% | [−8,5%, +8,5%] | ns |
  | 2 (`max_steps=30`) | as mesmas 19 | 42,1% | 57,9% | +15,8% | [−1,9%, +15,8%] | ns |
  | **3 (replicação)** | **41 inéditas** | 34,1% | 43,9% | **+9,8%** | [−3,5%, +16,7%] | ns |
  | agrupado *(secundário)* | 60 | 36,7% | 48,3% | **+11,7%** | **[+0,8%, +16,4%]** | **significativo** |

  Os +15,8% da corrida 2 foram uma varrida de 3–0 em três pares informativos, e a pré-registração deu
  a isso uma **chance de um em três de ser exatamente isso — uma amostra de sorte**, com a retratação
  pré-comprometida. A corrida 3 testou em **41 instâncias cujos resultados nunca tínhamos visto**, sem
  mudar mais nada. O efeito **reapareceu** (+9,8%, dentro da faixa registrada de +5 a +20) numa fatia
  que se mostrou *mais difícil* que a da corrida 2. Somando as duas, os pares discordantes ficam
  **9 a favor do Chimera contra 2** (p ≈ 2,6% sob a hipótese nula).

  **O mecanismo se replicou, e é a parte interessante.** Uma quarta corrida restaurou o braço do meio
  (scaffold puro, sem o portão de diff) nas mesmas 41 instâncias, de modo que os três diferem por
  exatamente um componente. Os três **editam na mesma taxa** (27–28 patches de 41); o que muda é com
  que frequência a edição está *certa*:

  | braço | resolvidas | **precisão quando editou** |
  |---|---|---|
  | baseline | 14/41 | 50% |
  | + scaffold | 16/41 | 59% |
  | + scaffold **e** portão de diff | 18/41 | 67% |

  **Os dois componentes contribuem, em metades aproximadamente iguais** (+4,9% cada, nenhum
  significativo isoladamente) — o que **contradiz nossa própria previsão registrada** de que o scaffold
  carregaria a maior parte, e retira uma leitura da corrida 2 de que o portão de diff "não é o que
  produziu o ganho". A retratação está no [`RESULTS.md`](bench/swe_bench/RESULTS.md); a aditividade
  bonitinha *não* é alegada como uma divisão 50/50 medida, já que cada comparação se apoia em 5–6
  pares discordantes.

  ⚠️ Leia com honestidade: **o primário fora da amostra NÃO é significativo.** O número significativo
  é o **secundário agrupado**, pré-registrado como secundário justamente porque mistura dados vistos
  com inéditos — ele não é promovido a manchete agora que cruzou a linha. E **48,3% NÃO é uma nota de
  SWE-bench Verified**: é uma fatia deliberadamente fácil, de um único repositório; uma nota de verdade
  precisa das 500 completas. O zero exato da corrida 1 é publicado sem alteração, e a corrida 2 trouxe
  a **retratação que merecia** (o mecanismo que havíamos alegado para os patches vazios estava errado —
  a cura era o orçamento de passos).
  Fonte: [`bench/swe_bench/RESULTS.md`](bench/swe_bench/RESULTS.md), [`PREREGISTRATION.md`](bench/swe_bench/PREREGISTRATION.md).
- **Terminal-Bench (humilhante).** A/B pré-registrado com N=40 no benchmark oficial, mesmo modelo nos
  dois braços (`deepseek-chat-v3.1`): **7,5% → 2,5%** com o scaffold, **Δ pareado −5,0pp, IC 95%
  [−5,0%, +1,6%] — não significativo**. O scaffold **não elevou um modelo já competente** (não é o
  regime fraco de "goldilocks" onde scaffolding ajuda); os dois braços ficam num piso dominado por
  variância. Fonte: [`bench/terminal_bench/RESULTS.md`](bench/terminal_bench/RESULTS.md).
- **O aprendizado acumulado ajuda? Sete corridas dizem: não comprovadamente (e um positivo foi
  retratado).** O volante — skills condicionadas a recorrência + um teste de transferência, cards de
  antipadrão, memória persistente — foi medido em **sete corridas pré-registradas**. A corrida 6
  produziu o único positivo da série (+6,7% significativo na métrica de transferência dentro da
  família); **a corrida 7, com mais poder estatístico, reduziu isso a +2,0% e não significativo — então
  foi retratado**, exatamente como a pré-registração havia comprometido. O veredito honesto: **nenhuma
  corrida com poder adequado mostra que o aprendizado acumulado melhora o sucesso nas tarefas**, e o
  gargalo é o instrumento — três tentativas de escrever uma suíte que caísse na faixa informativa de
  40–60% saíram todas em 84–92%. "Ele melhora quanto mais você usa" continua **sem evidência**.
  Fonte: [`bench/learning_lift/RESULTS.md`](bench/learning_lift/RESULTS.md).

Significativo internamente (na nossa própria suíte difícil). Em repositórios reais, **replicado fora
da amostra e significativo apenas quando agrupado** — o rótulo honesto, não o lisonjeiro. Humilhante
no Terminal-Bench. A alegação de aprendizado está **retratada**. Publicamos tudo, escrevemos de
antemão o ramo em que o resultado mata nossa própria alegação *antes* de rodar, e não refazemos
corridas atrás de significância — isso seria p-hacking.

## Economia de tokens — medida, não alegada

Dois instintos do tipo "mais modelos = melhor", colocados à prova em execuções reais (previsões
registradas *antes* de cada execução, vitórias **e** derrotas publicadas — veja [`bench/`](bench/)):

**A fusão é reservada, não o padrão.** Em uma suíte de raciocínio com 12 tarefas, o tier intermediário
sozinho marcou 100% com 846 tokens; a fusão completa também marcou 100% — por **9.526 tokens (~11×)**.
Então a fusão fica atrás de uma cascata barato→gate→intermediário→fusão que só escala quando um gate
gratuito falha, atingindo qualidade ~intermediária a ~1/12 do custo da fusão.

**A orquestração hierárquica só vence onde deve — e por uma lei que dá para escrever.**
O `chimera orchestrate` divide uma tarefa entre workers de escopo restrito, em vez de um único
contexto gigante. Um único agente reenvia cada documento a cada turno; workers de escopo restrito
leem cada um só uma vez. Então a economia de tokens escala como **(D−1)/D** no número de documentos D
— confirmado em execuções reais a <0,2%:

| documentos (D) | economia de tokens medida | (D−1)/D |
|---|---|---|
| 2 | 49,9% | 50% |
| 3 | 66,7% | 66,7% |
| 4 | 74,8% | 75% |
| 5 | 79,9% | 80% |

A economia se mantém estável conforme a conversa se alonga e cresce com o tamanho do documento na
direção do mesmo limite ([varredura completa, 3 eixos](bench/hierarchy_sweep/README.md)). E onde *não*
compensa — uma tarefa de tiro único com um só turno — o classificador detecta isso e **volta para um
único agente** (aquela execução custou +47% de tokens a mais; publicamos também).

**O asterisco honesto.** Essas são contagens de *tokens*. Com prompt caching, um provedor cobra os
documentos repetidos do único agente a ~0,1×, então a vitória em *dólar* é menor — e, passados alguns
turnos, ela pode **inverter** (workers independentes repagam contexto frio que o único agente coloca
em cache). Nós lançamos o [modelo que quantifica isso](bench/hierarchy_sweep/cache_cost.py) em vez de,
sorrateiramente, apresentar o número de tokens como se fosse o número em dólar.

## Recursos

### 🧠 Pensar & fazer
- **Combine vários modelos em uma resposta** (`chimera fuse`) — um painel de modelos, um juiz que revela onde eles concordam, discordam ou deixam algo passar, e um sintetizador que escreve a resposta final. Um roteador inteligente só gasta esse esforço extra em problemas difíceis, e quando os primeiros modelos já concordam ele para mais cedo — medido em **~20–28% menos tokens sem perda de precisão** em nossos benchmarks. (Fusão / mixture-of-agents em si não é exclusividade nossa — você encontra no OpenRouter e em outras ferramentas; a diferença aqui é que ela fica embutida no loop do agente, atrás desse roteador consciente de custo, e é medida, não um modelo que você escolhe.)
- **Conclua tarefas sozinho** (`chimera solve`) — ele planeja, age com ferramentas e então **verifica e reverte**: roda a sua checagem (por exemplo, testes) e só mantém a mudança se ela passar, senão desfaz e tenta de novo. Opcionalmente trabalha em uma cópia isolada do seu projeto, para que nada seja tocado até estar comprovado. **E um parágrafo convincente não é uma solução:** sem um `--verify` a que recorrer, uma execução que não mudou nada em disco é reportada como falha, não como sucesso — porque a única coisa restante para julgá-la seria um modelo lendo prosa, que nunca vê o diff. Cada tentativa registra *quem* a aprovou (`verifier` / `diff+manager` / `manager` / `none`), então um recibo nunca diz "sucesso" sem nomear a autoridade por trás.
- **Times de especialistas** (`chimera crew`, `chimera crew-isolated`) — vários agentes com papéis específicos dividem uma tarefa. No modo isolado, cada um trabalha em sua **própria cópia privada em paralelo**; edições seguras são mescladas, conflitos são sinalizados em vez de sobrescritos em silêncio, e as mudanças de um worker ruim podem ser rejeitadas por um teste próprio dele. Um supervisor pode juntar o trabalho de todos em um relatório unificado.
- **Delegar e explorar** — qualquer agente pode passar uma subtarefa autocontida para um **subagente** novo, que devolve apenas o resultado, mantendo limpo o contexto principal. O **Explorador de Contexto** (`chimera explore`) encontra os arquivos e as linhas certas em uma base de código e retorna uma resposta curta em vez de despejar tudo.

### 🧬 Memória & autoaperfeiçoamento
- **Memória de longo prazo** — ele guarda memórias de curto prazo, recentes, factuais e sobre você, além de um mapa de como as coisas se relacionam. Pode armazenar memórias em um banco de dados de busca textual rápido, levar um perfil das suas preferências para cada conversa, mesclar notas duplicadas automaticamente e sugerir gentilmente salvar uma preferência quando você menciona uma.
- **Aprende novas skills** — quando tem sucesso no mesmo tipo de tarefa mais de uma vez, ele transforma isso em uma skill testada e reutilizável automaticamente.
- **Autotreinamento opcional (avançado)** — ele pode registrar a própria experiência para que você possa, depois, ajustar (fine-tune) um modelo a partir dela. Desligado por padrão; nada é treinado sem você pedir.

### 📏 Um loop que dá para medir — e que avisa quando se perdeu
Um agente é um modelo **mais tudo que existe em volta dele**. Essa maquinaria ao redor é o que decide
se uma execução longa continua útil, e quase tudo nela é invisível até falhar. O Chimera mede a sua:

- **Toda execução deixa um recibo.** Uma linha JSONL por execução em `traces.jsonl`: tokens por passo, as ferramentas chamadas com o que voltou, onde o histórico foi descartado — e a **taxa de acerto de cache**, a fatia dos tokens de prompt que o provedor serviu do cache. Esse último é o número de custo real do loop (um token cacheado custa cerca de um décimo de um novo, então contagens idênticas podem diferir ~10× no preço) *e* um alarme de projeto: ele despenca sempre que algo reescreve o começo do prompt, o que não tem outro sintoma. Um provedor que não reporta cache lê como **desconhecido**, nunca como erro.
- **Ele percebe quando parou de chegar a algum lugar.** Duas coisas diferentes são chamadas de "problema de contexto": a atenção se diluindo dentro de um prompt longo, e uma *trajetória* que silenciosamente para de acumular e começa a girar — cada passo isolado bem, a execução como um todo indo a lugar nenhum. O detector de ciclo do Chimera pega a versão apertada (uma janela de 12 chamadas); uma execução que revisita os mesmos três arquivos a cada vinte turnos atravessa isso sem disparar. Então existe um segundo detector comparando a **primeira metade da execução com a segunda**: trabalho re-derivado que ela já tinha, falhas subindo, ou redundância pulando logo depois que histórico foi descartado. Ele **reporta e não age** — parar, re-planejar e forçar compactação são todas curas plausíveis e não temos evidência de qual ajuda, então escolher uma embutiria exatamente a suposição não-medida que este trabalho existe para remover.
- **Execuções longas sobrevivem ao próprio contexto.** Estourar a janela costumava encerrar a execução de vez, o que fazia da janela — e não da dificuldade da tarefa — o teto real. A compactação agora mantém a mensagem de sistema intacta (é o prefixo estável em que todo o cache de prompt está ancorado), nunca deixa um resultado de ferramenta órfão da chamada, e **restaura o que a execução precisa para continuar sendo ela mesma**: o arquivo aberto, o plano, a lista de tarefas, o estado atual. Ela diz claramente o que descartou em vez de resumir — um agente pode reler um arquivo, mas não consegue desacreditar um resumo inventado.

### 🔌 Conectar & automatizar
- **Fale com ele em qualquer lugar** — um chat no terminal, um app de tela cheia no terminal ou como um bot no **Discord, Telegram, Slack, Signal e WhatsApp**. Também há um endpoint HTTP simples.
- **Agendamento & proatividade** — dê tarefas recorrentes em linguagem simples ("toda manhã, resuma as notícias"). Com o agendador embutido rodando, ele **age na hora certa**, não só quando você manda mensagem.
- **Ferramentas & integrações** — ler e escrever arquivos, rodar comandos de shell, **ler páginas web totalmente renderizadas e fazer scraping ou crawling de sites inteiros** (com extração estruturada à prova de injeção) e executar código com segurança em um sandbox. Conecte quase qualquer serviço web (pela API dele) ou ferramenta externa — incluindo qualquer **servidor MCP** ([guia + exemplo executável](docs/mcp.md)) — e importe sua configuração de outras ferramentas de agente que você já usa.
- **Já vem com tudo** — busca na web, geração de imagens (hospedada **ou totalmente local**), **fala para texto** e texto para fala, **download de mídia**, **análise de dados & gráficos**, e-mail, calendário, execução de código e mais, prontos para ativar.

### 🚀 Rode em qualquer lugar, com segurança
- **Qualquer modelo, uma interface** — modelos hospedados ou os seus próprios modelos locais, com fallback automático se um estiver fora do ar e rotação entre várias chaves.
- **Deploy em servidor com um comando** — rode com Docker (ou direto na máquina) para que ele fique no ar e reinicie ao ligar o servidor. Veja **[docs/deploy.md](docs/deploy.md)**.
- **Kernel de segurança** — uma checagem em toda ação (permitir / avisar / bloquear / perguntar), um container de rede isolada **opt-in** para código não confiável (`CHIMERA_SANDBOX=docker`; o runner local padrão *não* é isolado) e um log de auditoria completo do que ele fez.
- **Pare antes de ele finalizar, quando ele leu algo em que não se deve confiar** (`--pause-on-taint`) — uma execução que consumiu conteúdo não confiável se estaciona em vez de finalizar, e espera por você. Você pode aceitar o resultado, aceitar uma versão que você editou, mandar orientação e deixá-lo tentar de novo, ou rejeitar de vez — pelo terminal *ou* pelo app de desktop. Nada é salvo e nada é aprendido até você decidir, e uma pausa nunca é reportada como falha: ela não chegou a um veredito, está esperando uma pessoa.
- **Um app de desktop que pilota uma execução, não só a dispara** — cinco destinos em vez de um menu de quinze, em nove idiomas. Inicie uma execução e saia dali: o progresso continua lá quando você voltar, a barra de status nomeia o que o agente está fazendo em qualquer tela, e o Parar funciona de todas. Instaladores nativos para Windows / macOS / Linux em [Releases](https://github.com/brcampidelli/chimera-agent/releases).

## Início rápido

Você precisa de **Python 3.11–3.13** e do [uv](https://docs.astral.sh/uv/) (um instalador Python rápido).

**1. Instale** — pelo PyPI:
```bash
pip install chimera-agent
```
Isso te dá o comando `chimera`. (Os exemplos abaixo usam `uv run chimera` para quem clonou o
repositório — com o pip install, é só `chimera …`.) Para desenvolver o próprio Chimera, clone o repo:
```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev
```

**2. Adicione a chave de um provedor de IA.** O mais fácil é uma chave do [OpenRouter](https://openrouter.ai) — uma
chave libera mais de 100 modelos.
```bash
cp .env.example .env
# abra o .env e defina, por exemplo:  CHIMERA_OPENROUTER_KEYS=sk-or-...
```

**3. Confira se está tudo pronto**
```bash
uv run chimera doctor
```

**4. Experimente**
```bash
uv run chimera chat                         # converse (ele lembra)
uv run chimera run "Explain what you can do in 3 bullets"
uv run chimera fuse "What's the best way to learn to cook?" --show-panel   # veja vários modelos combinados
uv run chimera solve "add a hello() function to app.py and a test for it" --verify "pytest -q"
```

**Rode em um servidor (para que trabalhe 24/7):**
```bash
docker compose up -d      # gateway + agendador; reinicia automaticamente
```
Guia completo (Docker ou systemd, agendamento, backups, segurança): **[docs/deploy.md](docs/deploy.md)**.

**5. Faça algo real em 5 minutos: triagem de e-mails.** Aponte o Chimera para a sua caixa de entrada
e receba um resumo de dez segundos — somente leitura, classifica em URGENTE / PESSOAL / NEWSLETTER /
COLD-SALES e, opcionalmente, agenda isso toda manhã:
```bash
uv run chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```
Configuração + agendamento diário + ressalvas honestas: **[examples/email_triage/README.md](examples/email_triage/README.md)**.

## 🧰 O que o Chimera faz — e como ligar cada coisa

Chegou agora? O Chimera já funciona logo após `pip install chimera-agent` + uma chave de IA. Algumas
capacidades (ler documentos, ouvir áudio, fazer gráficos, baixar vídeo…) precisam de um pacote
opcional — chamado **"extra"** — e algumas precisam de uma chave de serviço. Esta seção lista **cada
capacidade, exatamente o que instalar e o comando para experimentar**. Sem exigir conhecimento prévio.

### Ligue tudo de uma vez
```bash
pip install 'chimera-agent[full]'     # toda funcionalidade não-GPU abaixo, num comando
```
Áudio e vídeo também precisam do **ffmpeg** no seu computador:
`macOS: brew install ffmpeg` · `Ubuntu/Debian: sudo apt install ffmpeg` · `Windows: choco install ffmpeg`.
Prefere instalação enxuta? Mantenha `pip install chimera-agent` e adicione só os extras que quiser
(veja a coluna "Precisa"). **Usando Docker? A imagem oficial já vem com tudo abaixo.**

### Cada capacidade, ponto a ponto
**Precisa** = o que adicionar: `—` funciona na instalação básica · `[extra]` = `pip install 'chimera-agent[extra]'` · `chave: X` = uma chave de provedor no `.env`.

| O que você ganha | Precisa | Como usar |
|---|---|---|
| **Chat que lembra de você** | — | `chimera chat` |
| **Fazer uma pergunta** | — | `chimera run "explique X em 3 tópicos"` |
| **App de terminal em tela cheia** | — | `chimera tui` |
| **App de desktop** (chat · trabalho · código · conhecimento · automação, em 9 idiomas) | `[desktop]` ou um download | `chimera app`, ou baixe um instalador nativo (`.exe`/`.dmg`/`.AppImage`/`.deb`) em [Releases](https://github.com/brcampidelli/chimera-agent/releases) |
| **Fazer uma tarefa, e só manter se passar num teste** | — | `chimera solve "adicione hello() em app.py + um teste" --verify "pytest -q"` |
| **Me pergunte antes de finalizar qualquer coisa que ele leu da web** | — | acrescente `--pause-on-taint` ao `chimera solve` |
| **Ver o que uma execução custou de verdade, passo a passo** | — | já é escrito para você em `.chimera/traces.jsonl` (ou `$CHIMERA_HOME`) |
| **Fundir vários modelos numa resposta só** | — | `chimera fuse "sua pergunta" --show-panel` |
| **Um time de agentes especialistas** | — | `chimera crew "sua tarefa" --mode supervisor` |
| **Tocar um projeto inteiro até o fim** (pausa antes de passos arriscados) | — | `chimera project start spec.yaml -w .` |
| **Ver imagens** (visão) | chave: Gemini ou OpenAI | `chimera run --image foto.jpg "o que há aqui?" --model gemini/gemini-2.0-flash` |
| **Ouvir áudio** (fala → texto) | `[stt]` + ffmpeg | `chimera run "transcreva reuniao.mp3"` |
| **Falar** (texto → fala) | chave: ElevenLabs ou OpenAI | peça a qualquer tarefa "leia isto em voz alta para speech.mp3" |
| **Ler documentos** (PDF, Word, Excel → texto) | `[documents]` | `chimera run "resuma relatorio.pdf"` |
| **Baixar vídeo/áudio** (YouTube + 1000+ sites) | `[media-dl]` + ffmpeg | `chimera run "baixe o áudio de <url>"` |
| **Analisar dados e fazer gráficos** | `[data,viz]` | `chimera run "carregue vendas.csv e faça um gráfico da receita mensal"` |
| **Buscar na web** | chave: Tavily | `chimera run "busque na web: a versão mais recente do Python"` |
| **Ler e raspar páginas web reais** (um navegador de verdade) | — | `chimera run "abra example.com e me diga o título"` |
| **Memória de longo prazo** | — | `chimera memory add "..."` · `chimera memory search "..."` |
| **Aprender skills reutilizáveis sozinho** | — | acontece durante o `chimera solve`; liste com `chimera skills` |
| **Agendar trabalho recorrente** | — | `chimera cron add brief "0 8 * * *" "resuma as notícias"` |
| **Rodar como bot de chat** (Discord/Telegram/Slack/Signal/WhatsApp) | `[messaging]` | `chimera serve --cron --discord` |
| **Conectar qualquer ferramenta externa** (MCP) | `[mcp]` | guia: [docs/mcp.md](docs/mcp.md) |
| **Gerar imagens** (na nuvem) | chave: OpenAI | peça a uma tarefa "gere uma imagem de …" |
| **Gerar imagens** (100% local, precisa de GPU) | `[imagegen-local]` | igual, offline |

> Instale extras individualmente se quiser algo enxuto — `messaging`, `mcp`, `documents`, `media-dl`,
> `stt`, `data`, `viz`, `youtube` (todos incluídos no `full`), além do `imagegen-local` e `train` (só GPU).
> Exemplo: `pip install 'chimera-agent[documents,stt]'`.

### Primeira vez? Seis passos para iniciantes
1. **Instale o Python 3.11–3.13** ([python.org](https://www.python.org/downloads/)); confira com `python --version`.
2. **Instale o Chimera:** `pip install 'chimera-agent[full]'` (ou só `chimera-agent` para o núcleo enxuto).
3. **Pegue uma chave de IA** — uma chave do [OpenRouter](https://openrouter.ai) é a mais fácil (uma chave → 100+ modelos).
4. **Dê a chave ao Chimera:** copie `.env.example` para `.env` e defina `CHIMERA_OPENROUTER_KEYS=sk-or-...`.
5. **Verifique se está pronto:** `chimera doctor` — ele diz o que está configurado e o que falta.
6. **Experimente:** `chimera chat`.

Daqui pra frente, qualquer comando da tabela acima já funciona. Referência completa de comandos com
exemplos para copiar e colar: **[docs/usage.md](docs/usage.md)**.

## Como funciona

Dê uma tarefa ao Chimera; ele planeja (trazendo à tona as skills embutidas mais relevantes), pensa
(combinando modelos quando o problema é difícil), age com ferramentas — lendo e fazendo scraping da
web, editando arquivos, criando gráficos —, **confere o próprio trabalho e mantém só o que passa** e
então aprende com o resultado — realimentando memória e novas skills na próxima tarefa.

```mermaid
flowchart TD
    U([Você: uma tarefa ou uma pergunta]) --> P[Entender & planejar]
    P --> Q{É um problema difícil?}
    Q -- sim --> FUSION[Consultar vários modelos<br/>· um juiz os compara<br/>· um sintetizador escreve a melhor resposta]
    Q -- não --> ONE[Usar um modelo rápido]
    FUSION --> ACT[Agir: usar ferramentas, arquivos,<br/>ler & fazer scraping da web, criar gráficos,<br/>ou delegar a subagentes]
    ONE --> ACT
    ACT --> V{Funcionou?<br/>rodar testes / checagens}
    V -- sim --> KEEP[Manter a mudança]
    V -- não --> REVERT[Desfazer & tentar de novo com a lição aprendida]
    REVERT --> ACT
    KEEP --> LEARN[Aprender: salvar o que importa na memória,<br/>transformar trabalho repetido em skill reutilizável]
    LEARN --> U
    MEM[(Memória de longo prazo)] -. relembra .-> P
    LEARN -. escreve .-> MEM
    SKILLS[(Biblioteca de skills)] -. traz skills relevantes .-> P
    GOV[[Checagem de segurança em toda ação]] -. protege .-> ACT
```

## Comandos

Todo comando é `chimera <nome>` (ou `uv run chimera <nome>` antes de instalar).

```bash
chimera doctor / models / features    # verifica setup, lista modelos, vê capacidades opcionais
chimera chat                          # assistente interativo que lembra entre turnos
chimera tui                           # app full-screen no terminal
chimera run "PROMPT" --image pic.png  # resposta única (pode ler uma imagem)
chimera fuse "PROMPT" --show-panel    # combina vários modelos: painel -> juiz -> sintetizador
chimera solve "TASK" --verify "pytest -q" --isolate   # faz uma tarefa; mantém a mudança só se a checagem passar
chimera crew "TASK" --mode supervisor         # um time de especialistas encara uma tarefa
chimera crew-isolated "TASK" -W "name:role" --verify "..." --synthesize   # time, cada um em sua própria cópia isolada
chimera explore "where is login handled?"     # encontra os arquivos/linhas certos, dá uma resposta curta
chimera deliver "a launch plan" -o plan.md    # produz um documento caprichado
chimera serve --cron [--discord|--telegram|--slack|--signal]   # roda como serviço: bot de chat + agendador
chimera cron add "brief" "0 8 * * *" "Summarize the news"       # agenda trabalho recorrente
chimera memory add / graph / consolidate      # memória de longo prazo: salvar, relacionar, organizar
chimera kanban add/board/run                   # um quadro de tarefas que despacha trabalho para o agente
chimera workflow flow.yaml                     # roda uma automação repetível descrita em um arquivo
chimera migrate <source> <dir> --apply         # importa config, skills e memória de outra ferramenta de agente
chimera evolve status / tune / recipe          # opcional: auto-otimizar; preparar dados para fine-tune de um modelo
chimera fusion-bench / skillcard-bench / schema-bench / sandbox-bench   # benchmarks A/B honestos: mede custo, qualidade e efeitos colaterais antes de confiar em um recurso
chimera pet new --name Chimi                   # adote um pequeno companheiro virtual :)
```

Veja o **[Guia de Uso](docs/usage.md)** para cada comando com exemplos prontos para copiar e colar.

## Arquitetura

O Chimera é um pacote Python com partes bem separadas, para que você possa entender ou estender
qualquer pedaço isoladamente:

```
chimera/
  core/          o loop do agente: planejar, agir, verificar, manter-ou-desfazer, e cópias de trabalho isoladas
  fusion/        o motor "muitas mentes": painel -> juiz -> sintetizador + o roteador inteligente
  memory/        memória de curto prazo / recente / factual / sobre-você + um grafo de relacionamentos
  skills/        a biblioteca de skills embutida e como as skills relevantes são encontradas
  evolution/     aprender novas skills a partir do sucesso, e a experiência com que aprende
  governance/    o kernel de segurança (permitir/avisar/bloquear/perguntar), log de auditoria e controles de mudança
  orchestration/ times de agentes: papéis, crews, workers paralelos isolados, relatórios unificados
  ecosystem/     autoaperfeiçoamento avançado: agentes que projetam agentes, treino de modelo opcional
  kanban/        um quadro de tarefas que entrega cards ao agente
  workflow/      descreva uma automação repetível em um arquivo simples e rode-a
  tools/         ferramentas embutidas (arquivos, shell, web, busca) + execução de código
  sandbox/       roda ferramentas localmente ou dentro de um container isolado
  integrations/  conecta ferramentas externas e qualquer API web
  scheduler/     tarefas recorrentes + o daemon que as dispara na hora certa
  migration/     traga sua configuração de outras ferramentas de agente
  providers/     uma interface para todo modelo, com fallback e rotação de chaves
  interface/     o motor de conversa compartilhado (usado pelo chat, pelo app e pelos bots)
  server/        o gateway de mensageria e o endpoint HTTP
  cli/           o comando `chimera`
```

Veja [docs/architecture.md](docs/architecture.md) para o design completo.

## Visão & objetivos

**O objetivo do Chimera é simples: um agente de IA que qualquer um pode rodar, que raciocina melhor
ao combinar muitos modelos em vez de confiar em um só, que de fato melhora quanto mais é usado e que
se mantém seguro e totalmente aberto durante o caminho.**

A maioria das ferramentas de IA hoje é ou esperta-mas-esquecida (perdem tudo quando a conversa
termina) ou capaz-mas-fechada (você não as controla). E muitas que tentam "se aperfeiçoar" acabam,
silenciosamente, ficando *piores* ao longo do tempo. O Chimera é a nossa tentativa de um caminho
diferente:

- **Pensar melhor, sem uma conta maior** — combinar vários modelos só quando ajuda, para que a qualidade suba sem desperdício.
- **Memória de verdade e skills de verdade** — lembrar o que importa e transformar trabalho repetido em habilidades reutilizáveis.
- **Melhoria que dura** — resistir à lenta degradação que corrói outros agentes, conferindo o próprio trabalho e guardando o estado com segurança fora do modelo.
- **Seguro e transparente** — toda ação é verificável, e as destrutivas perguntam antes.
- **Aberto a todos** — gratuito, licenciado sob Apache-2.0, movido pela comunidade, sem lock-in.

É cedo (alpha), e a honestidade importa para nós: ele ainda não está comprovado em uso pesado de
produção. Se essa visão te empolga, adoraríamos sua ajuda para chegar lá.

## Desenvolvimento

```bash
git clone https://github.com/brcampidelli/chimera-agent.git
cd chimera-agent
uv sync --extra dev

uv run ruff check .      # estilo/lint
uv run mypy chimera      # checagem de tipos rigorosa
uv run pytest -q         # a suíte de testes
```

Contribuições são muito bem-vindas — código, docs, ideias, relatos de bugs. Comece pelo
[CONTRIBUTING.md](CONTRIBUTING.md) e pelo nosso [Código de Conduta](CODE_OF_CONDUCT.md).
Quer ensinar algo novo ao Chimera? O **[guia de extensão](docs/extending.md)** mostra como adicionar
sua própria **ferramenta, skill ou receita** (com exemplos para copiar e colar). Encontrou um problema
de segurança? Veja [SECURITY.md](SECURITY.md).

## Comunidade

Tem uma pergunta, uma ideia ou quer contribuir? **[Junte-se a nós no Discord](https://discord.gg/ACvBbrmguV)** — todo mundo é bem-vindo.

Prefere Reddit? Acompanhe **[r/ChimeraAgent](https://www.reddit.com/r/ChimeraAgent/)** para novidades e discussões.

## Apoie o projeto

O Chimera é gratuito e open-source, feito de forma aberta. Se ele te for útil, você pode ajudar a
financiar o desenvolvimento com uma doação única — toda ajuda faz diferença e é muito bem-vinda. 💜

**[💜 Doar via Stripe](https://buy.stripe.com/9B6aEQ57q91m1Gp7Lz77O01)**

## Licença

[Apache-2.0](LICENSE) — livre para usar, modificar e construir em cima.
