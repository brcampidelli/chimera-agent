---
source_sha256: bec93da7acb7d3a5290f324d0095c1da6c043ab5acb014a8cbcf4a63ada31510
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

### Preguntarle a la programación lo que no te está contando

```bash
chimera cron doctor
```

Una programación puede quedarse callada de dos maneras, y hasta que preguntas, ambas se ven igual —
como una programación sin nada pendiente:

- **No se ejecutó nada.** El demonio murió, el contenedor nunca se reinició, el host se durmió. Ni
  excepción, ni línea de log, ni veredicto. Todos los demás mecanismos de honestidad de aquí están
  *aguas abajo de que una ejecución haya ocurrido*, así que a ninguno le toca el turno.
- **Todo se ejecutó y todo falló.** El demonio está vivo, `last_run` fue hace un minuto, la
  programación avanza — y cada despacho lleva un mes fallando. Este caso se lee como *más sano* que
  el primero, porque el campo que parece indicar salud registra el intento, no el resultado.

`cron doctor` hace ambas preguntas y da consejos distintos, porque los arreglos no tienen nada en
común: atrasado es cosa del demonio, fallando es cosa del trabajo. `chimera cron list` imprime una
línea cuando algo está fallando, así que no necesitas saber que el comando existe para enterarte.

**Lo que no es.** Es una pregunta, no un vigilante: mientras el proceso está caído aquí nadie se
entera de nada, por la misma razón por la que un proceso que se cayó no puede registrar su propia
caída. Responde con honestidad en el momento en que algo pregunta — una shell, la aplicación, el
siguiente arranque. Un vigilante de verdad necesita su propio reloj y su propia vitalidad, lo cual
es una decisión aparte y está
[registrado como issue #26](https://github.com/brcampidelli/chimera-agent/issues/26). Si quieres una
alerta en lugar de una respuesta, ejecútalo desde el cron del propio host:

```cron
*/30 * * * * cd /opt/chimera && .venv/bin/chimera cron doctor | mail -s "chimera" you@example.com
```

Eso funciona porque lo supervisa algo que no es Chimera — que es justamente el punto.

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
  variable no hay ninguna — restringe el puerto, o expón solo la ruta del webhook. Para llegar a
  esta instancia desde la aplicación de escritorio, consulta
  [§5](#5-reaching-this-instance-from-the-desktop-app).
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

## 5. Llegar a esta instancia desde la aplicación de escritorio

Por defecto, la aplicación de escritorio habla con la Chimera que ella misma arranca en tu máquina.
Desde la v0.44 también puede apuntar a una que ejecutes tú — este VPS — y así la aplicación se
convierte en una ventana al agente que ya pasa la noche entera haciendo tus trabajos cron.

**Lee esta parte antes de abrir un puerto.** Lo que estás exponiendo no es un panel de control. Cada
pantalla de esa aplicación es una superficie de mando: ejecuta shell, edita archivos, despacha un
tablero de tareas autónomas y cambia ajustes. Una instancia accesible desde internet sin token no es
"una Chimera que alguien podría mirar" — es una máquina en la que cualquiera que encuentre la
dirección puede ejecutar comandos, pagados con tus claves de proveedor.

Tienen que cumplirse tres cosas, y sin las dos primeras la aplicación se niega a conectarse:

**1 — TLS.** Ponla detrás de un proxy inverso con un certificado de verdad (Caddy consigue uno por
ti):

```caddyfile
chimera.seudominio.com {
    reverse_proxy 127.0.0.1:8765
}
```

La aplicación rechaza una dirección sin `https` fuera de tu propia máquina, porque el token viaja en
una cabecera `Authorization` en **cada** petición — sobre http plano eso es una credencial entregada
a cada salto entre tú y el servidor, y nada en pantalla se vería mal mientras ocurre.

**2 — Un token.** La autenticación es opcional y está vacía por defecto:

```bash
CHIMERA_SERVER_TOKEN=$(openssl rand -hex 32)
```

Ponlo en el `.env`, reinicia y pega el mismo valor en la aplicación. La aplicación rechaza una
dirección remota sin token por la razón de arriba: una instancia sin él está abierta a quien la
encuentre.

Fíjate en lo que el servidor deliberadamente **no** hace: cuando un cliente remoto pide la UI, sirve
la página *sin* el token. El token nunca se entrega por la red — lo copias a tu propio cliente, una
vez, por fuera del canal. Por eso la aplicación tiene un campo para él.

**3 — El origen de tu aplicación.** La aplicación la sirve su propio sidecar local, así que sus
peticiones a esta instancia son de origen cruzado y un navegador descarta las respuestas mientras
esta instancia no nombre ese origen:

```bash
CHIMERA_ALLOWED_ORIGINS=http://127.0.0.1:45813
```

La aplicación te muestra el valor exacto cuando una conexión falla — está en el mensaje de error,
listo para copiar. El puerto es estable por instalación (se recuerda entre arranques desde la
v0.43), así que esto se configura una vez por cada máquina desde la que te conectas. Varios se
separan por comas.

**Este ajuste no es una frontera de seguridad y no debe leerse como tal.** CORS decide qué *página*
puede leer una respuesta; no decide nada sobre quién puede *llamar*. La barrera es el token. Nombrar
un origen sin configurar un token no protege nada — solo hace que una instancia desprotegida sea
accesible desde un navegador además de desde `curl`.

Vacío por defecto, así que una instancia que nadie configuró se comporta exactamente como antes.

### Qué te dice la aplicación cuando falla

- **"El token fue rechazado"** — la dirección y el origen son correctos; el valor está mal.
- **"No se pudo alcanzar"** — o la dirección está mal o el origen no está permitido. El navegador se
  niega a decir cuál de los dos, a propósito, así que la aplicación nombra ambos en vez de adivinar
  y te entrega el origen que hay que permitir.
- **Un aviso de versión** — la aplicación compara la versión de su propio backend con esta y dice
  ambos números. No se niega: un servidor una versión por detrás suele funcionar, y negarse te
  dejaría varado en la pantalla que necesitarías para arreglarlo. Puede que algunos endpoints no
  existan en el lado más viejo.

### Aún más seguro

Sáltate el puerto público por completo: llega al VPS por WireGuard o por una tailnet de Tailscale y
apunta la aplicación a la dirección privada. El token sigue importando — una tailnet es una
habitación más pequeña, no una vacía.

---

## 6. Estado honesto

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
