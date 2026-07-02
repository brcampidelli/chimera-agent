<div align="center">

<img src="assets/logo-wide.png" alt="Logo do Chimera" width="460" />

# Chimera

**O agente de IA open-source que pensa com muitas mentes — e melhora a cada dia.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-entrar-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <b>Português</b> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

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
> que faz. É **alpha** — sólido e bastante testado (mais de 460 testes automatizados, checagem de
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
- 🛡️ **Seguro por design.** Toda ação arriscada passa antes por uma checagem de segurança, qualquer coisa destrutiva pede confirmação, e ele pode rodar código não confiável dentro de um sandbox isolado.
- 🔌 **Qualquer modelo, roda em qualquer lugar.** Use grandes modelos hospedados ou os seus próprios modelos locais por uma única interface — no seu notebook ou em um servidor de US$ 5, o tempo todo.
- 🧩 **Realmente seu.** Open-source, sem lock-in, sem precisar de conta de fornecedor. Você roda, você é dono, você pode mudar qualquer coisa.

## Recursos

### 🧠 Pensar & fazer
- **Combine vários modelos em uma resposta** (`chimera fuse`) — um painel de modelos, um juiz que revela onde eles concordam, discordam ou deixam algo passar, e um sintetizador que escreve a resposta final. Um roteador inteligente só gasta esse esforço extra em problemas difíceis.
- **Conclua tarefas sozinho** (`chimera solve`) — ele planeja, age com ferramentas e então **verifica e reverte**: roda a sua checagem (por exemplo, testes) e só mantém a mudança se ela passar, senão desfaz e tenta de novo. Opcionalmente trabalha em uma cópia isolada do seu projeto, para que nada seja tocado até estar comprovado.
- **Times de especialistas** (`chimera crew`, `chimera crew-isolated`) — vários agentes com papéis específicos dividem uma tarefa. No modo isolado, cada um trabalha em sua **própria cópia privada em paralelo**; edições seguras são mescladas, conflitos são sinalizados em vez de sobrescritos em silêncio, e as mudanças de um worker ruim podem ser rejeitadas por um teste próprio dele. Um supervisor pode juntar o trabalho de todos em um relatório unificado.
- **Delegar e explorar** — qualquer agente pode passar uma subtarefa autocontida para um **subagente** novo, que devolve apenas o resultado, mantendo limpo o contexto principal. O **Explorador de Contexto** (`chimera explore`) encontra os arquivos e as linhas certas em uma base de código e retorna uma resposta curta em vez de despejar tudo.

### 🧬 Memória & autoaperfeiçoamento
- **Memória de longo prazo** — ele guarda memórias de curto prazo, recentes, factuais e sobre você, além de um mapa de como as coisas se relacionam. Pode armazenar memórias em um banco de dados de busca textual rápido, levar um perfil das suas preferências para cada conversa, mesclar notas duplicadas automaticamente e sugerir gentilmente salvar uma preferência quando você menciona uma.
- **Aprende novas skills** — quando tem sucesso no mesmo tipo de tarefa mais de uma vez, ele transforma isso em uma skill testada e reutilizável automaticamente.
- **Autotreinamento opcional (avançado)** — ele pode registrar a própria experiência para que você possa, depois, ajustar (fine-tune) um modelo a partir dela. Desligado por padrão; nada é treinado sem você pedir.

### 🔌 Conectar & automatizar
- **Fale com ele em qualquer lugar** — um chat no terminal, um app de tela cheia no terminal ou como um bot no **Discord, Telegram, Slack, Signal e WhatsApp**. Também há um endpoint HTTP simples.
- **Agendamento & proatividade** — dê tarefas recorrentes em linguagem simples ("toda manhã, resuma as notícias"). Com o agendador embutido rodando, ele **age na hora certa**, não só quando você manda mensagem.
- **Ferramentas & integrações** — ler e escrever arquivos, rodar comandos de shell, navegar na web e executar código com segurança em um sandbox. Conecte quase qualquer serviço web (pela API dele) ou ferramenta externa, e importe sua configuração de outras ferramentas de agente que você já usa.
- **Já vem com tudo** — busca na web, geração de imagens, texto para fala, e-mail, calendário, execução de código e mais, prontos para ativar.

### 🚀 Rode em qualquer lugar, com segurança
- **Qualquer modelo, uma interface** — modelos hospedados ou os seus próprios modelos locais, com fallback automático se um estiver fora do ar e rotação entre várias chaves.
- **Deploy em servidor com um comando** — rode com Docker (ou direto na máquina) para que ele fique no ar e reinicie ao ligar o servidor. Veja **[docs/deploy.md](docs/deploy.md)**.
- **Kernel de segurança** — uma checagem em toda ação (permitir / avisar / bloquear / perguntar), um sandbox para código não confiável e um log de auditoria completo do que ele fez.

## Início rápido

Você precisa de **Python 3.11+** e do [uv](https://docs.astral.sh/uv/) (um instalador Python rápido).

**1. Instale**
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

## Como funciona

Dê uma tarefa ao Chimera; ele planeja, pensa (combinando modelos quando o problema é difícil), age
com ferramentas, **confere o próprio trabalho e mantém só o que passa** e então aprende com o
resultado — realimentando memória e novas skills na próxima tarefa.

```mermaid
flowchart TD
    U([Você: uma tarefa ou uma pergunta]) --> P[Entender & planejar]
    P --> Q{É um problema difícil?}
    Q -- sim --> FUSION[Consultar vários modelos<br/>· um juiz os compara<br/>· um sintetizador escreve a melhor resposta]
    Q -- não --> ONE[Usar um modelo rápido]
    FUSION --> ACT[Agir: usar ferramentas, arquivos, a web,<br/>ou delegar a subagentes]
    ONE --> ACT
    ACT --> V{Funcionou?<br/>rodar testes / checagens}
    V -- sim --> KEEP[Manter a mudança]
    V -- não --> REVERT[Desfazer & tentar de novo com a lição aprendida]
    REVERT --> ACT
    KEEP --> LEARN[Aprender: salvar o que importa na memória,<br/>transformar trabalho repetido em skill reutilizável]
    LEARN --> U
    MEM[(Memória de longo prazo)] -. relembra .-> P
    LEARN -. escreve .-> MEM
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
Encontrou um problema de segurança? Veja [SECURITY.md](SECURITY.md).

## Comunidade

Tem uma pergunta, uma ideia ou quer contribuir? **[Junte-se a nós no Discord](https://discord.gg/ACvBbrmguV)** — todo mundo é bem-vindo.

## Licença

[Apache-2.0](LICENSE) — livre para usar, modificar e construir em cima.
