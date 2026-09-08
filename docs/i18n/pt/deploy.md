---
source_sha256: bec93da7acb7d3a5290f324d0095c1da6c043ab5acb014a8cbcf4a63ada31510
---

# Implantando o Chimera em um servidor (VPS)

O Chimera roda como um processo **gateway** de longa duração. Adicione `--cron` e ele também
dispara jobs agendados em um relógio real, então ele *age no tempo* (não só quando lhe mandam
mensagem). Este guia cobre uma implantação em VPS de $5 de duas formas: **Docker Compose**
(recomendado) ou **systemd**.

O estado — memória de longo prazo, jobs de cron, trajetórias, o log de auditoria — vive em
`CHIMERA_HOME` (um diretório). Persista-o (um volume Docker ou um caminho real) e o agente
sobrevive a reinicializações.

---

## 0. Pré-requisitos

- Um VPS Linux (1 vCPU / 1 GB de RAM é suficiente para um único agente).
- Ao menos uma chave de provedor. O jeito mais barato de começar é uma chave OpenRouter.
- Para webhooks públicos de entrada (WhatsApp Cloud API, `POST /webhook/<hook>`), um domínio +
  um reverse proxy com TLS (Caddy ou nginx). Não é necessário para Discord/Telegram/Slack/Signal,
  que se conectam por saída.

Crie seu arquivo de env a partir do template e preencha uma chave:

```bash
cp .env.example .env
# edit .env — e.g. set CHIMERA_OPENROUTER_KEYS=sk-or-...
```

---

## 1. Docker Compose (recomendado)

```bash
docker compose up -d       # build + run; restarts on crash and on reboot
docker compose logs -f     # watch it
docker compose ps          # status + health
```

Isso executa `chimera serve --host 0.0.0.0 --cron`: o gateway HTTP (`/chat`, `/webhook/<hook>`,
`/health`) **mais** o daemon de cron. O estado persiste no volume `chimera-data`.

**Servindo uma plataforma de chat** (Discord no exemplo) — defina o token no `.env`, depois
sobrescreva o comando em `docker-compose.yml`:

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

e rode `docker compose up -d` de novo. (Telegram/Slack/Signal funcionam da mesma forma via suas
flags; cada um precisa do seu token `CHIMERA_*` correspondente — veja `.env.example`.)

**Atualizar para uma nova versão:**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd (sem Docker)

Instale em um virtualenv no host:

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

Crie `/etc/systemd/system/chimera.service`:

```ini
[Unit]
Description=Chimera Agent gateway + cron daemon
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/chimera
EnvironmentFile=/opt/chimera/.env
Environment=CHIMERA_HOME=/opt/chimera/state
ExecStart=/opt/chimera/.venv/bin/chimera serve --host 0.0.0.0 --cron
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chimera
sudo systemctl status chimera
journalctl -u chimera -f
```

---

## 3. Agendando trabalho proativo (o daemon `--cron`)

`--cron` só *executa* os jobs que você agendou. Adicione-os com a CLI (eles persistem em
`CHIMERA_HOME`):

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Dentro do Docker:

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

O daemon pulsa a cada `--cron-tick` segundos (padrão 30) e despacha a ação de cada job através
do agente quando ela vence. Um job que falha é registrado e nunca para o daemon.

### Perguntar ao agendamento o que ele não está contando

```bash
chimera cron doctor
```

Um agendamento pode ficar em silêncio de duas maneiras, e até você perguntar, as duas parecem iguais
— como um agendamento sem nada a vencer:

- **Nada rodou.** O daemon morreu, o container nunca foi reiniciado, o host dormiu. Nenhuma exceção,
  nenhuma linha de log, nenhum veredito. Todo outro mecanismo de honestidade daqui fica *a jusante
  de uma execução ter acontecido*, então nenhum deles chega a ter a vez.
- **Tudo rodou e tudo falhou.** O daemon está vivo, `last_run` foi há um minuto, o agendamento
  avança — e todo despacho falha há um mês. Este caso se lê como *mais saudável* que o primeiro,
  porque o campo que parece indicar saúde registra a tentativa, não o resultado.

`cron doctor` faz as duas perguntas e dá conselhos diferentes, porque os consertos não têm nada em
comum: atrasado é sobre o daemon, falhando é sobre o job. `chimera cron list` imprime uma linha
quando alguma coisa está falhando, então você não precisa saber que o comando existe para descobrir.

**O que ele não é.** É uma pergunta, não um vigia: enquanto o processo está caído, nada aqui
percebe, pela mesma razão pela qual um processo que caiu não consegue registrar a própria queda. Ele
responde com honestidade no instante em que qualquer coisa pergunta — um shell, o app, a próxima
partida. Um vigia de verdade precisa do próprio relógio e da própria vivacidade, o que é uma decisão
à parte e está
[registrado como issue #26](https://github.com/brcampidelli/chimera-agent/issues/26). Se você quer
um alerta em vez de uma resposta, rode isso a partir do cron do próprio host:

```cron
*/30 * * * * cd /opt/chimera && .venv/bin/chimera cron doctor | mail -s "chimera" you@example.com
```

Isso funciona porque é supervisionado por algo que não é o Chimera — que é justamente o ponto.

---

## 4. Saúde, backups, segurança

- **Saúde:** `GET /health` retorna `{"ok": true}`. O Compose já vem com um healthcheck ligado.
- **Backups:** faça backup do volume `chimera-data` (Docker) ou do diretório `CHIMERA_HOME`
  (systemd) — isso é todo o estado durável. Exemplo:
  `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **Segredos:** mantenha as chaves em `.env` (ignorado pelo git); nunca as embuta na imagem.
- **Exposição:** vincule o gateway a `0.0.0.0` só atrás de um firewall/reverse proxy. Defina
  **`CHIMERA_SERVER_TOKEN`** para exigir `Authorization: Bearer <token>` no gateway HTTP e na API
  desktop (a UI desktop recebe o token automaticamente só para clientes loopback, então uma
  instância exposta remotamente fica atrás da sua própria autenticação). A autenticação é opt-in e
  vazia por padrão, então sem essa variável não existe nenhuma — restrinja a porta, ou exponha só
  o caminho do webhook. Para alcançar esta instância pelo app de desktop, veja
  [§5](#5-reaching-this-instance-from-the-desktop-app).
- **Sandboxing:** defina `CHIMERA_SANDBOX=docker` para rodar as tools de shell/código em um
  container descartável em vez do host.
- **Execução em host desatendida:** desde 2026-07-20, uma execução headless **recusa** comandos de
  host sob o padrão `CHIMERA_HOST_EXEC=ask` (não há TTY para confirmar). Uma implantação que
  genuinamente precisa que o agente rode shell no host define `CHIMERA_HOST_EXEC=allow`
  deliberadamente; a opção mais segura é `CHIMERA_SANDBOX=docker`, onde o gate é pulado porque o
  container isola de verdade. Da mesma forma, o servidor de API arma o estreitamento de taint
  (`CHIMERA_TAINT_NARROW=1`): depois que o agente lê conteúdo não confiável, as tools de
  execução/escrita/saída falham de forma fechada. Defina como `0` para continuar agindo de forma
  autônoma.

---

## 5. Alcançando esta instância pelo app de desktop

O app de desktop fala, por padrão, com o Chimera que ele mesmo inicia na sua máquina. A partir da
v0.44 ele também pode apontar para um que você mesmo mantém — este VPS — e assim o app vira uma
janela para o agente que já passa a noite inteira fazendo seus jobs de cron.

**Leia esta parte antes de abrir uma porta.** O que você está expondo não é um painel. Cada tela
desse app é uma superfície de comando: ela roda shell, edita arquivos, despacha um quadro de tarefas
autônomas e muda configurações. Uma instância alcançável pela internet sem token não é "um Chimera
que alguém poderia olhar" — é uma máquina na qual qualquer um que ache o endereço pode rodar
comandos, pagos pelas suas chaves de provedor.

Três coisas precisam ser verdade, e sem as duas primeiras o app se recusa a conectar:

**1 — TLS.** Ponha-o atrás de um reverse proxy com certificado de verdade (o Caddy consegue um para
você):

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

O app recusa um endereço sem `https` fora da sua própria máquina, porque o token viaja em um header
`Authorization` em **toda** requisição — em http puro isso é uma credencial entregue a cada salto
entre você e o servidor, e nada na tela pareceria errado enquanto acontecesse.

**2 — Um token.** A autenticação é opt-in e vazia por padrão:

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

Coloque-o no `.env`, reinicie e cole o mesmo valor no app. O app recusa um endereço remoto sem token
pela razão acima: uma instância sem ele está aberta para quem a encontrar.

Repare no que o servidor deliberadamente **não** faz: quando um cliente remoto pede a UI, ele serve
a página *sem* o token. O token nunca é entregue pela rede — você o copia para o seu próprio
cliente, uma vez, fora de banda. É por isso que o app tem um campo para ele.

**3 — A origem do seu app.** O app é servido pelo seu próprio sidecar local, então as requisições
dele para esta instância são cross-origin e um navegador descarta as respostas enquanto esta
instância não nomear aquela origem:

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

O app mostra o valor exato quando uma conexão falha — ele está na mensagem de erro, pronto para
copiar. A porta é estável por instalação (ela é lembrada entre inicializações desde a v0.43), então
isso se define uma vez por máquina de onde você conecta. Várias são separadas por vírgula.

**Esta configuração não é uma fronteira de segurança e não pode ser lida como uma.** O CORS decide
qual *página* pode ler uma resposta; ele não decide nada sobre quem pode *chamar*. O gate é o token.
Nomear uma origem sem definir um token não protege nada — só faz uma instância desprotegida ficar
alcançável por um navegador além de por `curl`.

Vazia por padrão, então uma instância que ninguém configurou se comporta exatamente como antes.

### O que o app diz quando falha

- **"O token foi recusado"** — o endereço e a origem estão certos; o valor está errado.
- **"Não foi possível alcançar"** — ou o endereço está errado ou a origem não é permitida. O
  navegador se recusa a dizer qual dos dois, de propósito, então o app nomeia os dois em vez de
  chutar e te entrega a origem a permitir.
- **Um aviso de versão** — o app compara a versão do próprio backend com esta e diz os dois números.
  Ele não recusa: um servidor um release atrás costuma funcionar, e recusar deixaria você preso
  justamente na tela de que precisaria para consertar. Alguns endpoints podem não existir no lado
  mais velho.

### Mais seguro ainda

Pule a porta pública por completo: alcance o VPS por WireGuard ou por uma tailnet do Tailscale e
aponte o app para o endereço privado. O token continua importando — uma tailnet é um cômodo menor,
não um vazio.

---

## 6. Status honesto

O Chimera está em **alpha**. Isso implanta e roda, e o daemon de cron o torna proativo — mas ele
ainda **não tem quilometragem de produção**. Comece com crons de baixo risco, observe os `logs`, e
mantenha as salvaguardas de governança (`--guard` no `solve`, `CHIMERA_SANDBOX=docker`) em mente
para qualquer coisa que toque sistemas reais.

## Onde estas páginas são publicadas

Estes arquivos são a fonte da documentação em **chimeraagent.space**, que os renderiza direto
deste diretório no momento do build. Edite o markdown aqui e o site acompanha; não há uma segunda
cópia para manter sincronizada.

A configuração do MkDocs que costumava viver em `mkdocs.yml` foi removida. Ela estava completa —
tema, navegação, dez páginas — e nunca foi publicada: não havia workflow nem branch `gh-pages`,
então as instruções de deploy que costumavam ficar neste ponto descreviam um site que não existia.
Uma configuração que ninguém executa é pior do que nenhuma configuração, porque a próxima pessoa
edita a navegação dela e não consegue entender por que nada muda.
