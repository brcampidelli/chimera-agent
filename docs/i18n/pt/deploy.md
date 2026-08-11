---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
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
  o caminho do webhook.
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

## 5. Status honesto

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
