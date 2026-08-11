---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
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
