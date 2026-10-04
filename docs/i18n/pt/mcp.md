---
source_sha256: 8d58c911516554ef6b8021e3d94ff098d8b6ae874cf981bf2ebadc7a0c15f2a2
---

# Conectando servidores MCP

MCP (Model Context Protocol) é a forma padrão de plugar tools externas em um agente — GitHub,
sistemas de arquivo, Notion, bancos de dados, e centenas de outros servidores falam esse
protocolo. O Chimera tem um cliente MCP de primeira classe: as tools de qualquer servidor viram
tools comuns do Chimera, ficando no mesmo registro que as nativas, governadas pelas mesmas camadas
de allowlist/kernel/ledger.

## Instale o extra do cliente

O cliente MCP vive atrás de um extra opcional para manter o núcleo leve:

```bash
uv sync --extra mcp
```

A maioria dos servidores são pacotes Node, então você também precisa do `npx` (vem junto com o
Node.js).

## Smoke test de 60 segundos (sem credenciais)

O servidor de referência de filesystem não precisa de nenhum token — ele só expõe tools de
leitura/escrita sobre um diretório que você escolhe:

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

Entregue esse registro a um `Agent` (ou veja `examples/mcp_github.py` para o loop completo) e o
modelo já pode chamar as tools do servidor como qualquer outra.

## Um servidor de verdade: GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

Essa é a integração inteira: ~26 tools do GitHub (buscar repositórios, ler arquivos, listar
issues, criar PRs, ...) aparecem no registro. Versão executável de ponta a ponta:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## Como isso se encaixa nas camadas de segurança

As tools MCP são objetos `Tool` comuns, então tudo compõe:

- **Allowlist por sessão** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  concede só as tools MCP que essa execução precisa; as não concedidas nunca chegam ao modelo.
- **Kernel de governança** — `govern_registry(...)` controla chamadas MCP com
  allow/warn/review/block como qualquer comando de shell.
- **Ledger de taint** — envolva com `ledger_registry(...)` e os fetches MCP são registrados; note
  que hoje só as tools nomeadas em `FETCH_TOOLS` são auto-classificadas, então trate conteúdo MCP
  como não confiável e prefira rodar com a semântica `--taint --guard` quando o servidor busca
  dados externos.

## O Chimera *como* servidor MCP

O cliente acima permite que o Chimera chame outras tools. O inverso também funciona: rode o
Chimera **como** um servidor MCP para que qualquer cliente MCP — Claude Desktop, uma IDE, outro
agente — possa chamar o motor inteiro como três tools.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

Ele expõe:

| Tool | O que faz |
| --- | --- |
| `chimera_solve` | Resolve uma tarefa de forma autônoma com plano + verificar-ou-reverter; retorna a resposta. |
| `chimera_fuse` | Responde um prompt através do motor LLM-Fusion (painel → juiz → sintetizador). |
| `chimera_memory_search` | Busca na memória de longo prazo do Chimera e retorna os fatos principais. |

Aponte um cliente MCP para ele como um servidor stdio. Para o Claude Desktop, adicione à sua
config:

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` precisa de uma chave de provedor para `chimera_solve`/`chimera_fuse` (a busca de memória
funciona sem uma). Adicione `--fuse` para rotear os turnos profundos do solver através da fusão,
`--no-memory` para pular o recall. Como o stdio é o fio de transporte, todos os logs vão para o
stderr — o stdout carrega só o protocolo.

## Deixar o Claude operar o app desktop

`chimera serve --mcp` monta um agente próprio. `chimera mcp desktop` não monta nada: é um
controle remoto do app desktop **que você já tem aberto**, então o Claude vê as mesmas
conversas, execuções e aprovações que você, e o que ele inicia roda sob a governança do app,
nas telas do app.

1. No app, abra **Configurações → Claude** e ligue **Permitir que o Claude opere este app**.
2. Registre o servidor no Claude Code (ou ponha o mesmo comando na config do Claude Desktop):

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

Com a primeira chave ligada, o Claude pode ler e iniciar conversas (`desktop_send`),
execuções, lotes, quadros e jobs de cron, buscar e editar a memória, e ler arquivos e o estado
do git. Uma execução que ele inicia leva a postura que você configurou, e um pedido que tenta
ampliá-la — um comando `verify`, execução no host, outro agente, aprovação automática — é
recusado. As aprovações continuam com você: `desktop_approvals` só as lista. Quando um turno
para em uma, `desktop_send` volta na hora dizendo que está esperando por você, e o turno segue
no app; `desktop_job` informa como ele termina.

A segunda chave, **Controle total**, acrescenta `desktop_approve` (responder aprovações e etapas
com portão) e `desktop_settings` (editar configurações e a identidade do agente). Com ela ligada,
o Claude pode aprovar ações sem você — e uma página ou mensagem com injeção de prompt lida pelo
agente pode levá-lo a isso. Essas duas tools nem aparecem na lista enquanto ela está desligada, e
o app as recusa se forem chamadas mesmo assim.

Algumas decisões continuam suas, qualquer que seja a chave ligada. Qual modelo responde — cada
configuração de modelo, a cadeia de fallback, o painel, o juiz e o sintetizador da fusão, o modo
de custo, a cascata e as respostas verificadas — e se o app roda tarefas agendadas, o Claude só
pode *sugerir*: nada é gravado, e o app mostra um cartão com o valor atual e o proposto de cada
configuração, para você aprovar ou recusar ali. O Claude não consegue responder esse cartão por
nenhum caminho; se a configuração mudou antes da sua aprovação, nada é aplicado. E liberar
comandos numa pasta, rodar um comando no Runner, iniciar um bot de mensagens e salvar um agente
com as permissões de ferramentas dele são recusados de vez pela ponte: isso você faz no app.

O que nenhuma das chaves permite: ler ou gravar uma chave de API, token ou webhook. Edições de
configuração recusam nomes de credenciais, as rotas que carregam chaves ou links de
compartilhamento não são alcançáveis, arquivos de credenciais (`.env`, chaves privadas) não
podem ser lidos, gravados nem buscados, e todo resultado é limpo de valores de credenciais.
Também não dá para apontar um workspace para a pasta de dados do app, nem para uma pasta que a
contenha (sua pasta pessoal, por exemplo): é lá que ficam as respostas das aprovações, e um
arquivo gravado ali responderia uma.

Como conecta: enquanto a chave está ligada, o app grava `~/.chimera/desktop-bridge.json` (a URL
da sua API em loopback e um token aleatório; no POSIX legível só por você, no Windows dentro do
seu perfil). Desligar a chave ou fechar o app apaga o arquivo e aposenta o token. Com o app
fechado, toda tool responde "Chimera desktop is not running, or 'Allow Claude to operate this
app' is off in Settings." O Claude lista as tools ao conectar; depois de ligar ou desligar o
**Controle total**, reconecte o servidor (`/mcp` no Claude Code) para ver a lista nova.

## Falando A2A (agente → agente)

O MCP conecta agentes a *tools*; **A2A** (Agent2Agent, Linux Foundation) conecta agentes *uns aos
outros* — é nativo no LangGraph, CrewAI, e AutoGen. O Chimera também fala isso, então um
orquestrador LangGraph/CrewAI pode delegar uma tarefa ao Chimera e receber de volta um resultado
completo.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` adiciona duas rotas ao servidor HTTP:

| Rota | Propósito |
| --- | --- |
| `GET /.well-known/agent.json` | O Agent Card — identidade + skills anunciadas (solve, fuse). |
| `POST /a2a` | Ciclo de vida de tarefa JSON-RPC 2.0: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

Um cliente envia `message/send` com uma parte de texto; o Chimera roda o agente autônomo e
retorna uma tarefa `completed` (ou `failed`) carregando a resposta como uma mensagem de agente.
Ou ele envia `message/stream` e recebe um stream de **Server-Sent Events**: primeiro a tarefa em
estado `working`, depois a tarefa `completed`/`failed` assim que a execução termina — então um
orquestrador vê o progresso sem fazer polling. O agent card anuncia
`capabilities.streaming: true`.

**Escopo, com honestidade:** o stream atualmente emite dois eventos (working → final), não
deltas de token por passo, e push notifications não estão implementadas. Isso é um stream
conformante, sem necessidade de polling — suficiente para ser um nó de primeira classe e
transmissível em um app LangGraph/CrewAI.

## Resolução de problemas

- `TimeoutError: MCP server ... did not become ready` — o comando não iniciou. Rode a mesma
  linha `npx ...` manualmente em um terminal para ver o erro (token faltando, Node faltando,
  download do pacote na primeira execução sendo lento — aumente `connect_timeout`).
- `ModuleNotFoundError: mcp` — instale o extra: `uv sync --extra mcp`.
- Conflitos de nome de tool — sempre passe um `name_prefix`.
- A sessão roda o servidor como um subprocesso durante toda a vida do seu script; chame o
  `close()` da sessão do `connector` (ou simplesmente deixe o processo terminar) para encerrá-lo.
