---
source_sha256: 246125d328876b4fd350e3a6783228d0f7b21f3b63de1d88cb4b44c36de859e5
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

## A voz do agente em prompts e textos de bot

Prompts e modelos de bot não devem fazer o agente afirmar sentimentos, cuidado, amizade, um relacionamento
ou promessas sobre si. Diga o que ele fez, o que não conseguiu fazer e o quanto tem certeza. Incerteza
calibrada em primeira pessoa (por exemplo, “não tenho certeza, mas…”) é permitida e útil.

## As camadas

- **Kernel de governança** — toda chamada de tool governada é allow / warn / review / block. Um
  primeiro filtro barato de assinaturas de shell perigosas, não a fronteira.
- **Sandbox** — um container efêmero, sem rede (`CHIMERA_SANDBOX=docker`), que pode ser reforçado com gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`). O **comando de verificação** roda no mesmo sandbox que o shell do agente. Um verificador declarado em `.chimera/verify.toml` continua sendo um comando de shell: em um host não isolado, ele só roda se o projeto tiver uma concessão `shell` ativa e `CHIMERA_HOST_EXEC` não negar a execução no host. Sem essa concessão, ele se abstém; comandos inferidos de arquivos de build continuam passando pela confirmação de `CHIMERA_HOST_EXEC` e se abstêm se ela for recusada. Uma concessão do projeto permite o comando configurado, mas não torna isolado o sandbox do host (`CHIMERA_VERIFY_NETWORK=1` dá rede a um verificador no Docker; sandboxes de kernel não conseguem fazer isso, portanto um verificador que precisa de rede só roda no host se tiver autorização por outros meios). Trate a configuração e seu comando como conteúdo executável do projeto; revise-os antes de conceder acesso ao shell. Se um turno editar o verificador configurado, a verificação se abstém; o comando é capturado antes do turno e exige, antes da primeira execução, uma aprovação duradoura do proprietário vinculada ao seu hash SHA-256 exato (e novamente a cada alteração). Essa aprovação vincula apenas a string do comando, não seus efeitos: `pytest`, `make test` e `npm test` executam arquivos controlados pelo projeto, como `conftest.py`, receitas do `Makefile` e scripts de pacotes, que o agente também pode ter editado.
Uma chave opcional `setup` nesse arquivo nomeia um comando para preparar o ambiente do projeto. Ela exige aprovação própria do proprietário, vinculada ao hash exato, e a mesma concessão `shell` ativa do projeto. É executada no máximo uma vez por workspace e hash do comando; falhas são informadas ao agente sem abortar o turno. Aprovações e a marca de preparação ficam no home do Chimera, não no checkout. Concessões e hashes aprovados só são herdados por worktrees duradouros criados pelo Chimera para esse projeto registrado; worktrees Git arbitrários não os herdam. O registro de worktrees do aplicativo fica sob `CHIMERA_HOME`. Um processo que já tenha acesso ao shell do host pode escrever ali, assim como pode editar o registro de projetos.
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

### Compartilhar uma conversa com uma segunda pessoa

O app desktop pode compartilhar uma conversa de código por um **token que abre só aquela conversa** —
nunca o token do servidor. O dono emite um por convidado (`POST /api/code/sessions/{id}/share`) e pode
revogá-lo sozinho; apagar a conversa revoga todos. Um convidado alcança quatro rotas sob
`/guest/api/…`: ler a conversa, acompanhá-la ao vivo, enviar uma mensagem nela, ver quem está lá. Nada
mais responde a um token de compartilhamento, e o app de convidado não tem rota para responder a um
card de governança — esses ficam na tela do dono.

O app de convidado é também a **única** coisa servida quando o dono abre a porta da rede
(`POST /api/code/share/network`, fechada de novo com `DELETE`, nunca aberta ao iniciar): uma máquina na
LAN alcança essas quatro rotas e nada do que o token do servidor protege. Dito sem rodeios antes de
entregar um link: um convidado pode pedir ao agente qualquer coisa que o dono pediria, no projeto do
dono, com as ferramentas e o gasto do dono — sob as costuras padrão da requisição, e sem aprovar cards.

### Seu próprio celular ou navegador: parear um dispositivo

Um link de convidado é para outra pessoa. Para o dono no próprio celular existe o pareamento:
`chimera pair` (ou **Parear dispositivo** em Configurações › Acessos) imprime um código que vale
**uma vez, por cinco minutos**, com um QR do endereço de pareamento. Ele é digitado numa página
servida por um segundo listener na LAN — fechado a cada início e aberto só pelo dono
(`chimera pair --lan`, ou **Abrir** no card) — e vira um cookie de 30 dias (`HttpOnly`,
`SameSite=Strict`, `Secure` sobre https) assinado com uma chave derivada do segredo de dispositivos
do dono (`<home>/devices.key`). Cinco códigos errados anulam todo código vigente; cinco de um mesmo
endereço deixam esse endereço de fora por quinze minutos.

Um dispositivo pareado é o dono **para ler e conversar**: conversas, turnos, orientação, respostas às
perguntas do próprio agente e recusar uma aprovação. Ele não é a tela do dono. Aprovar continua
exigindo o código de cada pergunta (estudo 30, S30-30), e as rotas que concedem acesso, mudam
configurações, liberam pastas, iniciam bots ou param o serviço respondem `403` a um dispositivo. O
card de Acessos lista cada dispositivo com o último uso e a rede (`192.168.1.x`); **Revogar** encerra
um na hora, inclusive um stream ao vivo que ele tenha aberto, e **Revogar todos os dispositivos**
troca o segredo, de modo que nenhum cookie emitido antes disso é verificado. A ponte do desktop não
pode parear, emitir código, listar nem revogar.

Longe de casa, o Chimera **não roda relay próprio**. Se você quer alcançar o listener de
dispositivos de fora, coloque na frente dele um túnel que você controla —
[Tailscale](https://tailscale.com/) nos dois dispositivos, ou um [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
para `http://127.0.0.1:<port>` — e decida isso conscientemente: ele estende a mesma porta até onde
o túnel alcança. Nunca faça bind do próprio app em `0.0.0.0` para isso.

## O backend do app desktop

O backend do app escuta em loopback, e loopback não é uma pessoa: qualquer processo da máquina
alcança a porta.

- **Um token em toda rota.** O app desktop sempre inicia seu backend com um bearer token — um
  segredo novo a cada início, ou o `CHIMERA_SERVER_TOKEN` que o dono já definiu. Toda rota `/api/…`
  passa então a responder `401` sem ele, `/api/health` incluída. Só os arquivos estáticos do app e o
  app de convidado (`/guest/api/…`, que confere seus próprios tokens de compartilhamento) ficam
  abertos; a ponte MCP (`/api/bridge/…`) mantém o seu próprio token. O shell entrega o token por
  início ao backend no ambiente e à janela pelo script de inicialização do webview — nunca numa
  linha de comando, numa URL ou num arquivo. O que o backend inicia não o herda: o ambiente dos
  filhos do sandbox e aquele com que `gh` e `git` rodam descartam toda variável com nome de segredo
  (exceto `GH_TOKEN` e as outras credenciais próprias do gh).
- **Aprovar sem o código exige a tela.** Um token diz quem pode chamar a API, não que o dono está
  olhando. Um sim sem código (`POST /api/approvals/{id}`) só passa quando a requisição traz também a
  credencial de tela em `X-Chimera-Screen`, ao lado do bearer token. Ela é sorteada a cada início e
  fica em memória: nunca num arquivo, num registro, num log, no `.env` ou na resposta de uma rota,
  nunca igual ao bearer token, e é removida do ambiente ao ser lida (o shell a passa como
  `CHIMERA_SCREEN_TOKEN`). Qualquer token lido do disco — `service.token`, um `CHIMERA_SERVER_TOKEN`
  no `.env` — continua precisando do código por pergunta para um sim; recusar não precisa de código.
  `chimera service` não tem credencial de tela, então ali todo sim pede o código. `chimera app` num
  navegador a põe na página que serve a uma requisição de loopback (`<meta name="chimera-screen">`),
  exatamente onde e quando essa página já leva o bearer token — a mesma exposição, por decisão do
  dono de 16/10/2026.
- **O serviço em segundo plano.** `chimera service start` roda o mesmo backend sem janela. Ele
  publica `~/.chimera/service-<channel>.json` (0600, gravado de forma atômica; `CHIMERA_SERVICE_DIR`
  o muda de lugar) com o token persistente do dono (`<home>/service.token`, ou um
  `CHIMERA_SERVER_TOKEN` escolhido pelo dono), e um cliente só confia nesse arquivo depois que
  `GET /api/info` responde com o mesmo pid, versão, id e pasta de dados. Esse token abre a API
  inteira, então fica separado do da ponte: nada do lado MCP lê os arquivos do serviço, e a ponte os
  recusa como arquivos. O registro de inicialização do app instalado,
  `~/.chimera/desktop-install.json`, guarda só quatro variáveis de caminho — `CHIMERA_HOME`,
  `CHIMERA_WORKSPACE`, `CHIMERA_SHELL_PREFS`, `CHIMERA_SHELL_STATE` —, quem o grava recusa qualquer
  outro nome, e `start` o valida antes de executar qualquer coisa a partir dele.

### Controle total pela ponte

A ponte do desktop (`chimera mcp desktop`) tem três chaves, cada uma sem efeito sem a anterior:
**Permitir que o Claude opere este app**, **Controle total** e, desde a decisão do dono de
2026-10-18, **Controle total pela ponte**, em Configurações > Segurança.

- **Só a tela liga.** `CHIMERA_BRIDGE_FULL_CONTROL` não está na lista de configurações editáveis, então
  `PATCH /api/config` a recusa a qualquer um e o `settings.edit` da ponte a recusa pelo nome. A única
  rota, `POST /api/security/bridge-full-control`, recusa qualquer requisição encaminhada pela ponte e
  só liga com a credencial de tela; o token do dono sozinho é o `service.token`, legível em disco. A
  tela pede confirmação. Desligar não precisa da tela: só estreita.
- **O que acrescenta.** As decisões do dono (conceder comandos a uma pasta, salvar um agente com suas
  ferramentas, regras de aprovação lembradas, recusar tudo, lembrar um projeto, responder uma sugestão
  de configuração, plugins e marketplaces, limpeza de worktrees), as rotas que exigem o `confirm: true`
  da pessoa (restaurar, excluir e exportar um design, as limpezas de armazenamento, reiniciar e
  resetar), as configurações do dono — postura, proteções, aprovações, tetos, modelos e agendamento,
  gravadas em vez de sugeridas — e abrir uma tela do app (`ui.navigate`, um endereço conferido contra
  as telas do próprio app, nunca uma URL).
- **O que nunca acrescenta.** Credenciais: nenhuma chave, token, senha ou segredo OAuth é lido ou
  gravado; o Runner, adicionar um servidor MCP ou entrar nele continuam do dono, e o cofre de chaves, a
  leitura do `.env` pelo agente e a URL base do provedor não são graváveis. E quem alcança o app:
  pareamento, compartilhamento, a porta da LAN, o token do servidor, os bots e suas listas de acesso.
- **O kernel decide primeiro.** Uma aprovação responde a um REVIEW; um BLOCK do kernel nunca chega a
  quem aprova. As checagens por execução continuam: uma execução iniciada pela ponte usa os modelos e
  a postura configurados.
- **Visível e registrado.** Enquanto está ligado, toda tela mostra uma barra com **Desligar** em um
  clique, e toda chamada feita com essa autoridade grava uma entrada de auditoria (`via: "bridge"`,
  `scope: "full_control"`, a rota, o status e os ids — nunca um corpo ou um valor), listada na aba
  Segurança.
- **Limite honesto.** A chave fica no `.env`, como as outras duas: um processo que possa gravar esse
  arquivo a liga sem a tela. As ferramentas do agente e a ponte recusam o arquivo; o resto que roda
  como o dono, não.

## O que o dono decide, e o que um repositório não pode

- **Política de implantação, só de negação.** `<CHIMERA_HOME>/policy.json` (ou o arquivo que
  `CHIMERA_POLICY_FILE` indicar) guarda regras ordenadas `provider.use` / `integration.use` /
  `tool.use`; a última que casa decide. Um `allow` ali nunca concede nada: só desfaz um deny anterior
  do mesmo arquivo, então não pode reabrir `CHIMERA_TOOL_DENYLIST`, o piso de alcance nem um BLOCK do
  kernel. Um arquivo inválido nunca é aplicado pela metade: `serve`, `chat`, `assist`, `tui` e `acp`
  se recusam a iniciar, `doctor` falha, e o desktop inicia mas recusa toda ferramenta até ele ser
  corrigido. O agente não pode reescrevê-lo — as ferramentas de escrita recusam o arquivo onde quer
  que ele esteja, e a ponte não pode apontá-lo para outro lugar.
- **"Sempre neste projeto".** Num card de aprovação o dono pode lembrar um sim para um projeto: a
  ferramenta, ou, para um comando de shell, suas duas primeiras palavras. Ele responde depois às
  perguntas que casam, **inclusive depois de a execução ler conteúdo não confiável** — decisão do dono
  de 15/10/2026, sabendo que uma página hostil poderia conduzir um comando dentro desse prefixo. Ele
  só pula a pergunta: um BLOCK do kernel e o piso de alcance continuam vencendo, e um comando que
  encadeia, usa pipe ou redireciona (`;`, `&&`, `|`, `>`, `$(`…) nunca casa. Só o dono cria ou revoga
  um; a ponte não pode, a menos que o dono
  tenha ligado o controle total pela ponte (acima).
- **Referências nomeadas.** Pastas que o dono lista em `<CHIMERA_HOME>/references.json` são
  alcançáveis como `@alias/path` só pelas ferramentas de leitura (`read_file`, `list_dir`, `grep`,
  `glob`); toda ferramenta de escrita recusa um alias. Uma raiz do sistema de arquivos, a home do
  usuário ou a pasta de dados é recusada como referência. Só superfícies atendidas as recebem —
  `chat`, `assist`, `tui`, um `solve` atendido, o turno Code do desktop (nunca o de um convidado) —,
  então bots, cron e `scenarios` não têm nenhuma.
- **Agentes em Markdown.** `.chimera/agents/*.md` num repositório é texto de outra pessoa, então só
  pode restringir: suas ferramentas são intersectadas com as ferramentas governadas da sessão, seus
  passos são limitados pelos da sessão, chaves desconhecidas são recusadas, seu `model` é ignorado, e
  seu corpo é cercado como dado não confiável. Só os arquivos do dono em `<CHIMERA_HOME>/agents`
  podem fixar um modelo do catálogo.
- **Chats ligados a conversas.** `/link` no Discord ou no Telegram faz um chat continuar uma conversa
  Code, e só o dono listado na allowlist daquele bot pode ligar um — sem allowlist, ninguém. O texto
  de um estranho numa sala ligada nunca chega à conversa, e mídia é recusada em vez de repassada. O
  turno roda no piso do bot, não no do desktop: só os servidores MCP de `CHIMERA_BOT_MCP_SERVERS`,
  nenhum conector OpenAPI, nem concessão de shell nem host exec.
- **Os conectores do dono ficam fora das portas que um estranho abre.** Um conector OpenAPI chama
  para fora com a chave do dono, então responde a quem conduz a execução. Ele não carrega numa
  superfície de mensagens enquanto algum bot configurado estiver sem allowlist, nunca num job de
  issue do GitHub (qualquer pessoa escreve a issue; só o repositório é conferido), e em
  `/v1/chat/completions` só enquanto `CHIMERA_SERVER_TOKEN` estiver definido. Um módulo,
  `chimera/governance/surfaces.py`, decide que tipo de superfície um nome é, pela família
  (`platform:discord` é um bot); um teste falha se um módulo voltar a escrever sua própria lista.

## Limites honestos

Isto mede se a ação nociva de um agente *já injetado* é interrompida — não se o modelo pode ser
injetado em primeiro lugar. Raciocínio livre sobre prosa não confiável, e exfiltração através de
tools legitimamente necessárias, continuam sendo problemas em aberto (rastreados como
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

A política completa e sempre atualizada vive em
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), incluindo
como reportar uma vulnerabilidade.

## Limites do desvio de permissões da sessão

O desvio opcional de permissões da sessão de código é autenticado por uma chave HMAC gerada na memória do processo; essa chave nunca é gravada em disco nem herdada por processos filhos. Flags `bypass_permissions` persistidas e atestações `via_screen` enfileiradas sem um HMAC válido são ignoradas. Portanto, a escolha de permissões dura apenas até o reinício do processo do app/serviço; depois disso, o desvio fica desativado por padrão. Um shell ainda pode editar o JSON, mas não falsificar a atestação. Decisão do dono (26/10): com a chave ligada, o agente libera tudo o que, de outro modo, pediria confirmação — git e pushes para o GitHub, `gh`, ferramentas de conectores MCP, deploys, uploads, escritas HTTP e ferramentas de envio, tanto em execução limpa quanto contaminada. Três coisas ainda pedem confirmação ao dono, e nenhuma outra: (a) as regras BLOCK do kernel e a denylist destrutiva (`rm -rf /`, `mkfs`, ...); (b) enviar ou ler um arquivo secreto (`.env`, chaves, `.npmrc`, `.netrc`, `.pypirc`, `.git-credentials`, credenciais AWS) como parte de um comando de saída, e `-EncodedCommand` do PowerShell; (c) reescrever o histórico do branch padrão num remoto — um force push, um `+refspec`, ou a remoção de `main`/`master` (`git push --delete`, `git push origin :main`), inclusive quando escrito dentro de `bash -c` ou `powershell -Command`, e deleções via `gh api` da referência padrão. Force push para um branch de feature é liberado. Essas verificações são heurísticas léxicas, não uma fronteira de segurança: nomes expandidos por glob como `cat .e*`, strings montadas dinamicamente como `python -c "open('.'+'env')"`, codificações alternativas, wrappers ou comandos fora da sintaxe reconhecida não podem ser detectados com garantia. Uma página maliciosa ainda pode contorná-las. Use o sandbox isolado para trabalho não confiável.
