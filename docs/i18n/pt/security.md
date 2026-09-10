---
source_sha256: eeb0e80877d9cd939362a1d6f1b736437c3918f1b24f1fb1b44e18f31aa71e42
---

# Segurança & salvaguardas

O Chimera pode rodar comandos de shell, editar arquivos, chamar APIs, e modificar suas próprias
skills. Ele vem com **defesa em profundidade**, e — isso importa — a documentação declara onde
cada camada *para*.

!!! warning "A única regra"
    Nenhuma dessas salvaguardas substitui **rodá-lo em um ambiente isolado** quando você concede
    autonomia. O runner `local` padrão não é isolado; use
    `CHIMERA_SANDBOX=docker` (rede desligada, opcionalmente sob gVisor) para trabalho não
    confiável.

## As camadas

- **Kernel de governança** — toda chamada de tool governada é allow / warn / review / block. Um
  primeiro filtro barato de assinaturas de shell perigosas, não a fronteira.
- **Sandbox** — um container efêmero, sem rede (`CHIMERA_SANDBOX=docker`), reforçável com gVisor
  (`CHIMERA_SANDBOX_RUNTIME=runsc`).
  O **comando de verificação** roda no mesmo sandbox que o shell do agente, e quando esse sandbox
  não é isolado um comando que não foi digitado por você — inferido do repositório, lido de um job
  de cron, de um cartão ou de um workflow — passa pela mesma confirmação do `CHIMERA_HOST_EXEC`;
  recusado, ele se abstém em vez de rodar (`CHIMERA_VERIFY_NETWORK=1` dá rede a um verificador em
  docker; nos sandboxes de kernel isso não é possível, então um verificador que precisa de rede
  roda no host, e só quando foi você quem o digitou).
- **Allowlist de tools por sessão** — concede a uma execução só as tools de que ela precisa; o
  resto é removido inteiramente do schema do modelo.
- **Rastreamento de taint** (`--taint`) — conteúdo não confiável é cercado como dado, sua
  proveniência o segue até memórias e skills (uma skill de uma execução contaminada fica retida
  para review), e assim que uma execução é contaminada as tools perigosas se estreitam.
- **Leitor quarentenado** — o padrão dual-LLM / CaMeL: conteúdo não confiável é lido por um modelo
  sem tools que só pode emitir campos validados por schema, então uma injection não consegue
  produzir uma instrução ou chamada de tool nova.
- **Monitor entre agentes** — sob fan-out, um monitor por-trabalhador é cego a um fluxo *dividido*
  (um trabalhador busca conteúdo não confiável, um trabalhador diferente o consome — o fetch e o
  sink vivem em ledgers separados). Um monitor agregado enxerga o fan-out inteiro; ele está
  **sempre ligado** para `solve-batch` / `crew-isolated`.

## Fan-out: o monitor entre agentes

Quando vários trabalhadores que usam tools rodam em paralelo (`solve-batch`, `crew-isolated`),
cada um recebe seu próprio ledger de capacidades, e depois do lote um monitor agregado roda sobre
todos eles. Ele captura padrões que nenhum monitor de trabalhador único consegue ver — a
exfiltração dividida em que o trabalhador A busca conteúdo não confiável e o trabalhador B o
executa ou exfiltra:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

Ele sempre só **escala para review** — nunca bloqueia uma execução — e é observabilidade pura
(o registro não muda o comportamento). Adicione `--taint` por cima para também armar a allowlist
adaptativa de cada trabalhador (tools perigosas-quando-contaminadas passam a exigir aprovação).

**Aprovação, e como um trabalhador recusado aparece.** Cada trabalhador carrega o próprio
aprovador e o próprio registro do que teve permissão de fazer, então uma tarefa recusada diz
isso em vez de reportar `ok`:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

O `ok` entre parênteses é o veredito do próprio laço, e os dois discordarem é justamente o
ponto: uma chamada recusada volta como uma linha de observação comum, então o trabalhador a lê
como qualquer resultado de tool, segue em frente e termina em prosa. Quem pode ser perguntado
segue `CHIMERA_APPROVAL_MODE` — `deny` recusa na hora, `ask` pergunta num terminal e, fora
dele, anota a pergunta para o `chimera approve` e espera `CHIMERA_APPROVAL_WAIT` segundos, por
pergunta e por trabalhador. Silêncio sempre recusa.

## Medido, não afirmado

```bash
chimera redteam
```

roda um corpus de injection através da pilha. No corpus embutido, a camada de taint corta a
**taxa de sucesso de ataque de 100% para ~14%** — e o relatório *nomeia* o que ainda passa
(exfiltração via uma tool permitida) em vez de alegar 100%.

O mesmo comando imprime o **custo**, coisa que a primeira versão desta página não fazia: sem
ninguém a quem perguntar, o estreitamento recusa **100% do trabalho legítimo que leu qualquer
coisa externa primeiro** — consertar o arquivo que a issue nomeia, aplicar o upgrade que a
documentação descreve — e o gate registrado (over-block ≤ 5%) reprova. Esse número não é um
problema de ajuste; o gate estava vazio. O modo de aprovação padrão é `ask`, e no desktop ele
agora pergunta: uma chamada de tool estreitada vira uma pergunta na tela, com o motivo do ledger
anexado, respondida por um botão ou por `chimera approve`, recusada pelo silêncio depois de
`CHIMERA_APPROVAL_WAIT` segundos. Com a pessoa aprovando o trabalho que ela mesma pediu, o
over-block é 0% e a taxa de bloqueio de ataques não se move — medido, por braço, em
[`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md).
A exfiltração através de uma tool permitida é fechada pela mesma mudança: em uma execução
contaminada, um `http_get` que carrega uma query string é um review, e os dois GETs legítimos com
query string acrescentados ao corpus mostram o que isso custa.

### Memória envenenada, entre execuções

`redteam` mede uma execução. O outro formato é mais lento e não cabe dentro de um processo: a
execução A lê uma página envenenada e guarda o que "aprendeu"; a execução B faz uma pergunta sem
nenhuma relação dias depois, e a recuperação entrega o fato plantado ao modelo.

```bash
chimera memory-poison
```

Também offline e de graça. Ele desliga uma a uma as três camadas que ficam entre essas duas
execuções — o marcador de proveniência `tainted`, o gate de admissão da recuperação, e o rótulo
`[unverified]` que o fato veste ao entrar no prompt — porque um número único seria compatível com
qualquer uma delas não estar fazendo nada. A manchete é o que chega **sem marcação**, não o que é
bloqueado: um fato envenenado que carrega sua origem é um fato sobre o qual o modelo foi avisado;
um sem rótulo é indistinguível de algo que o próprio agente verificou.

Dois resultados da primeira execução merecem ser ditos sem rodeio, porque nenhum dos dois nos
favorece:

- **A configuração que enviamos reprova no próprio gate — no custo.** Ela marca 100% do veneno e
  destrói 25% da memória honesta ao fazer isso. As baixas estão nomeadas: um documento de segurança
  que cita um ataque para poder explicá-lo, e um ticket de suporte encaminhando uma tentativa. Um
  comparador de padrões sobre o conteúdo não distingue uma citação de um comando.
- **Neste corpus, o gate de conteúdo não acrescenta nada que o rótulo de proveniência já não
  cubra.** Todo o efeito medido dele é a memória honesta que ele remove. Quinze linhas escritas à
  mão são um indício e não um veredito, e é por isso que nada foi deletado com base nisso.

Os limiares, o método e o que os números *não* autorizam estão em
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md),
fixados antes da primeira execução.

## Expondo o servidor HTTP

`chimera serve` se vincula a `127.0.0.1` por padrão. Seus endpoints que alteram estado (`/chat`,
`/a2a`, `/webhook/*`) conduzem o agente, então **antes de expor o servidor a uma rede**, defina um
bearer token:

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

Com ele definido, esses endpoints POST retornam `401` sem um header `Authorization: Bearer`
correspondente (`GET /health` e o agent-card do A2A ficam abertos). Para o webhook de entrada do
WhatsApp, defina `CHIMERA_WHATSAPP_APP_SECRET` com o secret do seu app Meta — o Chimera então
verifica o HMAC `X-Hub-Signature-256` de cada requisição e rejeita um payload forjado com `403`.
Ambos são opt-in (não definido = sem autenticação, ok para localhost); uma implantação pública
deveria defini-los (ou ficar atrás de um proxy que autentique).

## Limites honestos

Isto mede se a ação nociva de um agente *já injetado* é interrompida — não se o modelo pode ser
injetado em primeiro lugar. Raciocínio livre sobre prosa não confiável, e exfiltração através de
tools legitimamente necessárias, continuam sendo problemas em aberto (rastreados como
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

A política completa e sempre atualizada vive em
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), incluindo
como reportar uma vulnerabilidade.
