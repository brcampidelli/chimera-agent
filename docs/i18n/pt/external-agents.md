---
source_sha256: 91c3e19b5cb75bc83cafd3408047b7f1df85f8cb28d2be4b66897ce7a2e6a1ba
---

# Agentes externos (ACP)

O Chimera pode entregar um turno de código a um agente que ele não escreveu — Claude Code, Gemini
CLI, ou qualquer adaptador que fale o [Agent Client Protocol](https://agentclientprotocol.com). O
transcript, o verificador, a cópia de segurança e o desfazer continuam sendo do Chimera; o trabalho é
de outro.

## Por quê

A tese do Chimera nunca foi que o loop dele é o único loop bom. É a governança em volta de um loop: o
registro de contaminação, a região de escrita, a cópia antes do turno, o veredito depois, o recibo
dizendo o que de fato aconteceu. Isso vale para qualquer executor. Recusar-se a dirigir um executor
em que você já confia seria insistir na metade menos interessante do produto.

## O que é garantido, e o que não é

Leia esta parte antes da instalação, porque é ela que decide se este recurso serve para você.

Um agente ACP declara quais capacidades do cliente vai usar, e o Chimera oferece
`fs/read_text_file` e `fs/write_text_file`. **Oferecer não é impor.** Os agentes que valem a pena
dirigir têm ferramentas de arquivo e de terminal próprias: o Claude Code escreve pelo Claude Agent
SDK, e não tem obrigação nenhuma de nos perguntar antes.

Concretamente:

| | Loop do próprio Chimera | Agente externo |
|---|---|---|
| Região de escrita recusa fora dela | Sempre | Só o que passa por nós |
| O shell roda no sandbox configurado | Sempre | O agente roda comandos do jeito dele |
| O registro de contaminação arma o portão | Sempre | Só nas ferramentas que mediamos |
| Cópia do workspace antes do turno | Sim | **Sim** |
| Desfazer o turno inteiro em um clique | Sim | **Sim** |
| Toda permissão concedida aparece no recibo | — | **Sim** |

As três últimas linhas são a garantia de verdade, e são o que a linha de postura da tela de Código
promete quando um agente externo está escolhido. Ela deixa de dizer "edita dentro de `/projeto`, não
roda comandos" — essa frase descreve ferramentas que o Chimera controla — e passa a dizer que uma
cópia foi tirada e que o turno pode ser desfeito. Uma tela que mantivesse a frase mais forte estaria
fazendo uma promessa que o turno não consegue cumprir.

O Chimera também **recusa** a capacidade de terminal do ACP. Um terminal hospedado por nós seria um
segundo caminho de execução ao lado do sandbox, sem nenhuma das regras dele.

## Instalação

Nada a configurar para os agentes que o Chimera conhece:

```bash
npm i -g @agentclientprotocol/claude-agent-acp   # Claude Code, precisa de Node 22+
npm i -g @google/gemini-cli                       # Gemini CLI (o modo ACP é experimental na origem)
```

Depois confira o que esta máquina realmente consegue rodar:

```bash
chimera doctor
```

`external_agents` reporta cada um com `available: true/false` e, quando falso, a linha que resolve.
A disponibilidade é resolvida na máquina onde o sidecar roda — que, num build empacotado de desktop,
é uma máquina montada pela CI que ninguém olhou. Ou seja: "deveria estar lá" não é evidência.

O app de desktop mostra uma linha **Quem executa** acima do compositor, listando o que o `doctor`
encontrou. Quando nada instalável está presente, a linha não aparece; o `doctor` é o lugar de "você
ainda não tem isso, e é assim que se instala".

## Credenciais

Todo processo filho que o Chimera lança recebe um ambiente sem as variáveis `API_KEY` / `TOKEN` /
`SECRET`, para que um comando de shell não consiga ecoar uma chave de provedor. Um agente ACP é um
programa cujo trabalho inteiro depende de uma dessas, então cada agente declara **pelo nome** as
variáveis de que precisa, e só elas voltam:

- Claude Code: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`
- Gemini CLI: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`

Passar o ambiente inteiro seria mais fácil e entregaria a todo adaptador futuro todas as chaves da
máquina.

## Um adaptador seu

O Codex e outros chegam ao ACP por adaptadores de terceiros que este projeto não rodou. Em vez de
listar um comando não verificado — o que transformaria "não conferimos" em "suportado" — aponte o
Chimera para o que você tem:

```jsonc
// POST /api/code/turn
{
  "message": "conserta o teste que está falhando",
  "provider": "custom",
  "provider_command": "npx -y algum-adaptador-acp --flag"
}
```

O comando é separado no estilo shell e executado **sem** shell, então um pipe perdido vira um
argumento e não um segundo comando. No Windows, um argumento com sintaxe do cmd.exe (`& | < > ^ %`)
chegando a um lançador `.cmd` é recusado em vez de escapado: as regras de aspas mudam de lançador
para lançador, e um palpite errado executa a sua máquina em vez de um programa nela.

## Como funciona

- Um processo filho por **conversa**, não por turno. Um `session/prompt` é uma mensagem dentro de um
  contexto que o agente guarda; um processo novo a cada vez faria de todo turno o turno um.
- No máximo quatro vivos ao mesmo tempo, e um parado há uma hora é encerrado. Cada um é um processo
  segurando uma conexão com o modelo.
- O processo nasce no próprio grupo e é morto como árvore — um agente de código é um lançador, e
  matar só o processo que seguramos deixaria os trabalhadores rodando e a pasta travada. Um reaper
  no `atexit` cobre o caso de fechar o app no meio de um turno.
- As notificações `session/update` do agente são traduzidas para os mesmos eventos que o loop nativo
  emite, então a tela não precisa de uma segunda implementação. Blocos de raciocínio são descartados
  em vez de misturados à resposta; um bloco `diff` vira o patch unificado que o transcript já mostra.
- Números que o loop nativo tem e este não — `steps`, `context_peak_tokens` — chegam como `null` e
  não como `0`. Zero se leria como "não fez nada".

## Limites

- Pedidos de permissão são respondidos com `allow_once` e **registrados no recibo**. Barrar um pedido
  que o agente não era obrigado a fazer é encenação; a versão honesta é conceder, registrar, e contar
  com a cópia de segurança — que também cobre as escritas que nunca perguntaram.
- Fusão, papéis, memória e o mapa do repositório são do loop do próprio Chimera. Um turno externo
  reporta `fused: false` e nenhum uso de memória porque nada disso aconteceu.
- O modo ACP do Gemini é marcado como experimental na origem e pode mudar de comportamento entre
  versões.
