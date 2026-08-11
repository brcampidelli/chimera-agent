---
source_sha256: 472844a34ca189775b1f61de23b6ed2a36820a935b8465106a9f283a134caa33
---

# Desplegar Chimera en un servidor (VPS)

Chimera se ejecuta como un proceso **gateway** de larga duración. Agrega `--cron` y también
dispara trabajos programados en un reloj real, así que *actúa a tiempo* (no solo cuando se le
envía un mensaje). Esta guía cubre un despliegue en un VPS de $5 de dos maneras: **Docker
Compose** (recomendado) o **systemd**.

El estado — memoria de largo plazo, trabajos cron, trayectorias, el registro de auditoría — vive
en `CHIMERA_HOME` (un directorio). Persístelo (un volumen Docker o una ruta real) y el agente
sobrevive a los reinicios.

---

## 0. Requisitos previos

- Un VPS Linux (1 vCPU / 1 GB de RAM es suficiente para un solo agente).
- Al menos una clave de proveedor. El inicio más barato es una clave de OpenRouter.
- Para webhooks entrantes públicos (WhatsApp Cloud API, `POST /webhook/<hook>`), un dominio +
  un proxy inverso con TLS (Caddy o nginx). No es necesario para Discord/Telegram/Slack/Signal,
  que se conectan de forma saliente.

Crea tu archivo de entorno a partir de la plantilla y rellena una clave:

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

Eso ejecuta `chimera serve --host 0.0.0.0 --cron`: el gateway HTTP (`/chat`, `/webhook/<hook>`,
`/health`) **más** el demonio de cron. El estado persiste en el volumen `chimera-data`.

**Servir una plataforma de chat** (se muestra Discord) — establece el token en `.env`, luego
sobreescribe el comando en `docker-compose.yml`:

```yaml
    command: ["serve", "--host", "0.0.0.0", "--cron", "--discord"]
```

y ejecuta `docker compose up -d` de nuevo. (Telegram/Slack/Signal funcionan igual mediante sus
respectivas flags; cada una necesita su token `CHIMERA_*` correspondiente — consulta
`.env.example`.)

**Actualizar a una nueva versión:**

```bash
git pull && docker compose up -d --build
```

---

## 2. systemd (sin Docker)

Instala en un virtualenv en el host:

```bash
git clone https://github.com/brcampidelli/chimera-agent.git /opt/chimera
cd /opt/chimera
python3 -m venv .venv && . .venv/bin/activate
pip install '.[messaging,mcp]'
cp .env.example .env   # then edit it
```

Crea `/etc/systemd/system/chimera.service`:

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

## 3. Programar trabajo proactivo (el demonio `--cron`)

`--cron` solo *ejecuta* los trabajos que has programado. Agrégalos con la CLI (persisten en
`CHIMERA_HOME`):

```bash
chimera cron add "morning-brief" "0 8 * * *" "Summarize overnight news and post it."
chimera cron add "nightly-backup" "0 3 * * *" "Back up the important files."
chimera cron list
```

Dentro de Docker:

```bash
docker compose exec chimera chimera cron add "morning-brief" "0 8 * * *" "..."
```

El demonio hace tick cada `--cron-tick` segundos (30 por defecto) y despacha la acción de cada
trabajo a través del agente cuando corresponde. Un trabajo que falla queda registrado y nunca
detiene el demonio.

---

## 4. Salud, respaldos, seguridad

- **Salud:** `GET /health` devuelve `{"ok": true}`. Compose tiene un healthcheck conectado.
- **Respaldos:** respalda el volumen `chimera-data` (Docker) o el directorio `CHIMERA_HOME`
  (systemd) — eso es todo el estado durable. Ejemplo: `docker run --rm -v chimera-data:/d -v $PWD:/b busybox tar czf /b/chimera-state.tgz -C /d .`
- **Secretos:** guarda las claves en `.env` (ignorado por git); nunca las incrustes en la
  imagen.
- **Exposición:** enlaza el gateway a `0.0.0.0` solo detrás de un firewall/proxy inverso.
  Configura **`CHIMERA_SERVER_TOKEN`** para exigir `Authorization: Bearer <token>` en el gateway
  HTTP y en la API de escritorio (la UI de escritorio recibe el token automáticamente solo para
  clientes loopback, así que una instancia expuesta remotamente permanece detrás de tu propia
  autenticación). La autenticación es opcional y está vacía por defecto, así que sin esa
  variable no hay ninguna — restringe el puerto, o expón solo la ruta del webhook.
- **Sandboxing:** configura `CHIMERA_SANDBOX=docker` para ejecutar las herramientas de shell/código
  en un contenedor desechable en lugar del host.
- **Ejecución desatendida en el host:** desde 2026-07-20 una ejecución headless **rechaza**
  comandos de host bajo el valor por defecto `CHIMERA_HOST_EXEC=ask` (no hay un TTY para
  confirmar). Un despliegue que genuinamente necesita que el agente ejecute shell en el host
  configura `CHIMERA_HOST_EXEC=allow` deliberadamente; la opción más segura es
  `CHIMERA_SANDBOX=docker`, donde la barrera se omite porque el contenedor realmente aísla. De
  igual manera, el servidor de la API arma el estrechamiento por taint (`CHIMERA_TAINT_NARROW=1`):
  después de que el agente lee contenido no confiable, las herramientas de ejecución/escritura/
  salida fallan de forma cerrada. Configúralo en `0` para seguir actuando de forma autónoma.

---

## 5. Estado honesto

Chimera está en **alpha**. Esto se despliega y funciona, y el demonio de cron lo hace proactivo
— pero todavía **no tiene kilometraje en producción**. Empieza con crons de bajo riesgo, observa
los `logs`, y ten presentes las salvaguardas de gobernanza (`--guard` en `solve`,
`CHIMERA_SANDBOX=docker`) para cualquier cosa que toque sistemas reales.

## Dónde se publican estas páginas

Estos archivos son la fuente de la documentación en **chimeraagent.space**, que los renderiza
directamente desde este directorio en tiempo de build. Edita el markdown aquí y el sitio lo
sigue; no hay una segunda copia que mantener sincronizada.

La configuración de MkDocs que solía vivir en `mkdocs.yml` fue eliminada. Estaba completa —
tema, navegación, diez páginas — y nunca se publicó: no había workflow ni rama `gh-pages`, así
que las instrucciones de despliegue que solían estar en este lugar describían un sitio que no
existía. Una configuración que nadie ejecuta es peor que ninguna configuración, porque la
próxima persona edita su navegación y no logra entender por qué nada cambia.
