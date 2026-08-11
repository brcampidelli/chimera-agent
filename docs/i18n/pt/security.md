---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
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

## Medido, não afirmado

```bash
chimera redteam
```

roda um corpus de injection através da pilha. No corpus embutido, a camada de taint corta a
**taxa de sucesso de ataque de 100% para ~14%** — e o relatório *nomeia* o que ainda passa
(exfiltração via uma tool permitida) em vez de alegar 100%.

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
