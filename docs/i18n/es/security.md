---
source_sha256: 246125d328876b4fd350e3a6783228d0f7b21f3b63de1d88cb4b44c36de859e5
---

# Seguridad y salvaguardas

Chimera puede ejecutar comandos de shell, editar archivos, llamar APIs, y modificar sus propias
skills. Viene con **defensa en profundidad**, y — esto importa — la documentación indica dónde
*se detiene* cada capa.

!!! warning "La única regla"
    Ninguna de estas salvaguardas reemplaza **ejecutarlo en un entorno aislado** cuando otorgas
    autonomía. El runner `local` por defecto no está aislado; usa
    `CHIMERA_SANDBOX=docker` (sin red, opcionalmente bajo gVisor) para trabajo no confiable.

## La voz del agente en prompts y textos de bot

Los prompts y las plantillas de bot no deben hacer que el agente afirme sentimientos, cuidado, amistad,
una relación o promesas sobre sí mismo. Di lo que hizo, lo que no pudo hacer y cuán seguro está. La
incertidumbre calibrada en primera persona (por ejemplo, «no estoy seguro, pero…») está permitida y es útil.

## Las capas

- **Kernel de gobernanza** — cada llamada a herramienta gobernada es allow / warn / review /
  block. Un primer filtro barato de firmas de shell peligrosas, no la frontera.
- **Sandbox** — un contenedor efímero, sin red (`CHIMERA_SANDBOX=docker`), que puede reforzarse
  con gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`). El **comando verify** se ejecuta en el mismo
  sandbox que la shell del agente. Un verificador declarado en `.chimera/verify.toml` sigue siendo
  un comando de shell: en un host no aislado solo se ejecuta si el proyecto tiene un permiso
  `shell` activo y `CHIMERA_HOST_EXEC` no deniega la ejecución en el host. Sin ese permiso se
  abstiene; los comandos inferidos de archivos de compilación siguen pasando por la confirmación
  `CHIMERA_HOST_EXEC` y se abstienen si se rechaza. El permiso del proyecto autoriza el comando
  configurado, pero no convierte el sandbox del host en uno aislado (`CHIMERA_VERIFY_NETWORK=1`
  da red a un verificador en Docker; los sandboxes del kernel no pueden hacerlo, así que un
  verificador que necesita red se ejecuta en el host solo si está autorizado por otros medios).
  Trata la configuración y su comando como contenido ejecutable del proyecto; revísalos antes de
  conceder acceso a la shell. Si un turno modifica el verificador configurado, la verificación se
  abstiene; el comando se captura antes del turno y requiere una aprobación duradera del
  propietario, vinculada a su hash SHA-256 exacto, antes de la primera ejecución (y de nuevo cada
  vez que cambie). Esta aprobación vincula solo la cadena del comando, no sus efectos: `pytest`,
  `make test` y `npm test` ejecutan archivos controlados por el proyecto, como `conftest.py`, las
  recetas de `Makefile` y los scripts del paquete, que el agente también puede haber editado.
Una clave opcional `setup` en ese archivo nombra un comando para preparar el entorno del proyecto. Requiere su propia aprobación del propietario vinculada al hash exacto y el mismo permiso `shell` activo del proyecto. Se ejecuta como máximo una vez por espacio de trabajo y hash del comando; los fallos se comunican al agente sin abortar el turno. Las aprobaciones y la marca de preparación se guardan bajo el directorio de Chimera, no en el checkout. Los permisos y hashes aprobados solo se heredan en worktrees duraderos creados por Chimera para ese proyecto registrado; los worktrees arbitrarios de Git no los heredan. El registro de worktrees de la aplicación se guarda bajo `CHIMERA_HOME`. Un proceso que ya tenga acceso de shell al host puede escribir allí, igual que puede editar el registro de proyectos.
- **Lista blanca de herramientas por sesión** — otorga a una ejecución solo las herramientas que
  necesita; el resto se elimina por completo del esquema del modelo.
- **Seguimiento de taint** (`--taint`) — el contenido no confiable se cerca como datos, su
  procedencia lo sigue hacia memorias y skills (una skill de una ejecución contaminada se retiene
  para revisión), y una vez que una ejecución está contaminada las herramientas peligrosas se
  restringen.
- **Lector en cuarentena** — el patrón dual-LLM / CaMeL: el contenido no confiable lo lee un
  modelo sin herramientas que solo puede emitir campos validados por esquema, así que una
  inyección no puede producir una nueva instrucción o llamada a herramienta.
- **Monitor entre agentes** — bajo fan-out, un monitor por worker es ciego a un flujo *dividido*
  (un worker obtiene contenido no confiable, un worker diferente lo consume — el fetch y el sink
  viven en ledgers separados). Un monitor agregado ve todo el fan-out; está **siempre activo**
  para `solve-batch` / `crew-isolated`.

## Fan-out: el monitor entre agentes

Cuando varios workers que usan herramientas corren en paralelo (`solve-batch`,
`crew-isolated`), cada uno recibe su propio ledger de capacidades, y después del lote un monitor
agregado corre sobre todos ellos. Detecta patrones que ningún monitor de un solo worker puede
ver — la exfiltración dividida donde el worker A obtiene contenido no confiable y el worker B lo
ejecuta o lo exfiltra:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

Solo **escala a revisión** — nunca bloquea una ejecución — y es pura observabilidad (registra
cambios, no cambia el comportamiento). Agrega `--taint` además para también armar la lista
blanca adaptativa de cada worker (las herramientas peligrosas-cuando-contaminadas entonces
requieren aprobación).

**Aprobación, y cómo se ve un worker rechazado.** Cada worker lleva su propio aprobador y su
propio registro de lo que se le permitió hacer, así que una tarea rechazada lo dice en vez de
reportar `ok`:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

El `ok` entre paréntesis es el veredicto del propio bucle, y que ambos discrepen es justamente
el punto: una llamada rechazada vuelve como una línea de observación cualquiera, así que el
worker la lee como cualquier resultado de herramienta, sigue adelante y termina en prosa. A
quién se puede preguntar lo decide `CHIMERA_APPROVAL_MODE` — `deny` rechaza de inmediato,
`ask` pregunta en una terminal y si no anota la pregunta para `chimera approve` y espera
`CHIMERA_APPROVAL_WAIT` segundos por pregunta y por worker. El silencio siempre rechaza.

## Medido, no afirmado

```bash
chimera redteam
```

ejecuta un corpus de inyección a través del stack. En el corpus incorporado, la capa de taint
reduce la **tasa de éxito de ataque del 100% al ~14%** — y el informe *nombra* lo que aún se
filtra (exfiltración vía una herramienta permitida) en lugar de afirmar el 100%.

El mismo comando imprime el **costo**, cosa que la primera versión de esta página no hacía: sin
nadie a quien preguntar, la restricción rechaza el **100% del trabajo legítimo que primero leyó
algo externo** — arregla el archivo que nombra el issue, aplica la actualización que describe la
documentación — y el gate registrado (sobrebloqueo ≤ 5%) falla. Ese número no es un problema de
ajuste; el gate estaba vacío. El modo de aprobación por defecto es `ask`, y en el escritorio ahora
sí pregunta: una llamada a herramienta restringida se convierte en una pregunta en pantalla con el
motivo del ledger adjunto, respondida con un botón o con `chimera approve`, rechazada por el
silencio después de `CHIMERA_APPROVAL_WAIT` segundos. Con la persona aprobando el trabajo que
pidió, el sobrebloqueo es 0% y la tasa de bloqueo de ataques no se mueve — medido, por brazo, en
[`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md).
La exfiltración a través de una herramienta permitida se cierra con el mismo cambio: el `http_get`
de una ejecución contaminada que lleva un query string es una revisión, y los dos GET legítimos con
query string agregados al corpus muestran lo que eso cuesta.

### Memoria envenenada, entre ejecuciones

`redteam` mide una sola ejecución. La otra forma es más lenta y no cabe en un proceso: la ejecución
A lee una página envenenada y guarda lo que "aprendió"; la ejecución B hace una pregunta sin
relación días después y el recall le entrega al modelo el hecho plantado.

```bash
chimera memory-poison
```

También offline y gratis. Hace ablación de las tres capas que están entre esas ejecuciones — la
marca de procedencia `tainted`, la puerta de admisión del recall, y la etiqueta `[unverified]` que
el hecho lleva puesta al entrar al prompt — porque un solo número sería compatible con que
cualquiera de ellas no hiciera nada. Lo que cuenta es lo que llega **sin marcar**, no lo que se
bloquea: un hecho envenenado que trae consigo su origen es un hecho del que el modelo fue
advertido; uno sin etiqueta es indistinguible de algo que el propio agente verificó.

Dos resultados de la primera ejecución merecen decirse con claridad, porque ninguno nos halaga:

- **La configuración que se entrega no pasa su propio gate — por costo.** Marca el 100% del veneno
  y destruye el 25% de la memoria honesta al hacerlo. Las bajas están nombradas: un documento de
  seguridad que cita un ataque para explicarlo, y un ticket de soporte que reenvía un intento. Un
  comparador de patrones sobre el contenido no puede distinguir una cita de un comando.
- **En este corpus el gate de contenido no agrega nada que la marca de procedencia no cubra ya.**
  Todo su efecto medido es la memoria honesta que elimina. Quince filas escritas a mano son una
  pista y no un veredicto, y por eso no se ha borrado nada sobre esa base.

Los umbrales, el método y lo que los números *no* autorizan están en
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md),
fijados antes de la primera ejecución.

## Exponer el servidor HTTP

`chimera serve` se enlaza a `127.0.0.1` por defecto. Sus endpoints que cambian estado (`/chat`,
`/a2a`, `/webhook/*`) manejan al agente, así que **antes de exponer el servidor a una red**,
configura un token bearer:

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

Con eso configurado, esos endpoints POST devuelven `401` sin un encabezado
`Authorization: Bearer` que coincida (`GET /health` y la agent-card de A2A permanecen abiertos).
Para el webhook entrante de WhatsApp, configura `CHIMERA_WHATSAPP_APP_SECRET` con el secreto de
tu app de Meta — Chimera entonces verifica el HMAC `X-Hub-Signature-256` de cada solicitud y
rechaza una carga útil falsificada con `403`. Ambos son opcionales (sin configurar = sin
autenticación, correcto para localhost); un despliegue público debería configurarlos (o estar
detrás de un proxy que autentique).

### Compartir una conversación con una segunda persona

La app de escritorio puede compartir una conversación de código mediante un **token que abre solo esa
conversación** — nunca el token del servidor. El dueño emite uno por invitado
(`POST /api/code/sessions/{id}/share`) y puede revocarlo por separado; borrar la conversación los revoca
todos. Un invitado alcanza cuatro rutas bajo `/guest/api/…`: leer la conversación, seguirla en vivo,
enviar un mensaje en ella, ver quién está. Nada más responde a un token de compartición, y la app de
invitado no tiene ruta para responder a una tarjeta de gobernanza — esas quedan en la pantalla del dueño.

La app de invitado es también lo **único** que se sirve cuando el dueño abre la puerta de red
(`POST /api/code/share/network`, cerrada de nuevo con `DELETE`, nunca abierta al arrancar): una máquina en
la LAN alcanza esas cuatro rutas y nada de lo que protege el token del servidor. Dicho sin rodeos antes de
entregar un enlace: un invitado puede pedirle al agente cualquier cosa que el dueño le pediría, en el
proyecto del dueño, con sus herramientas y su gasto — bajo las costuras por defecto de la petición, y sin
aprobar tarjetas.

### Tu propio teléfono o navegador: vincular un dispositivo

Un enlace de invitado es para otra persona. Para el dueño en su propio teléfono está la vinculación:
`chimera pair` (o **Vincular un dispositivo** en Ajustes › Accesos) imprime un código que funciona
**una vez, durante cinco minutos**, con un QR de la dirección de vinculación. Se teclea en una página
servida por un segundo listener de la LAN — cerrado en cada arranque y abierto solo por el dueño
(`chimera pair --lan`, o **Abrir** en la tarjeta) — y se convierte en una cookie de 30 días
(`HttpOnly`, `SameSite=Strict`, `Secure` sobre https) firmada con una clave derivada del secreto de
dispositivos del dueño (`<home>/devices.key`). Cinco códigos erróneos anulan todo código vigente;
cinco desde una misma dirección dejan fuera esa dirección durante quince minutos.

Un dispositivo vinculado es el dueño **para leer y hablar**: conversaciones, turnos, orientación,
respuestas a las propias preguntas del agente y rechazar una aprobación. No es la pantalla del dueño.
Aprobar sigue exigiendo el código de cada pregunta (estudio 30, S30-30), y las rutas que conceden
acceso, cambian ajustes, otorgan carpetas, arrancan bots o detienen el servicio responden `403` a un
dispositivo. La tarjeta de Accesos lista cada dispositivo con su último uso y su red (`192.168.1.x`);
**Revocar** termina uno al instante, incluido un stream en vivo que tenga abierto, y **Revocar todos
los dispositivos** sustituye el secreto, de modo que ninguna cookie emitida antes se verifica. El
puente del escritorio no puede vincular, emitir un código, listar ni revocar.

Fuera de casa, Chimera **no ejecuta ningún relay propio**. Si quieres alcanzar el listener de
dispositivos desde fuera, pon delante un túnel que tú controles — [Tailscale](https://tailscale.com/)
en ambos dispositivos, o un [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
hacia `http://127.0.0.1:<port>` — y decídelo a conciencia: extiende la misma puerta hasta donde
llegue el túnel. Nunca enlaces la propia app a `0.0.0.0` para esto.

## El backend de la app de escritorio

El backend de la app escucha en loopback, y loopback no es una persona: cualquier proceso de la
máquina alcanza el puerto.

- **Un token en cada ruta.** La app de escritorio siempre arranca su backend con un bearer token — un
  secreto nuevo en cada arranque, o el `CHIMERA_SERVER_TOKEN` que el dueño ya definió. Toda ruta
  `/api/…` responde entonces `401` sin él, `/api/health` incluida. Solo los archivos estáticos de la
  app y la app de invitado (`/guest/api/…`, que comprueba sus propios tokens de compartición) quedan
  abiertos; el puente MCP (`/api/bridge/…`) conserva su propio token. El shell entrega el token de
  cada arranque al backend en el entorno y a la ventana mediante el script de inicialización del
  webview — nunca en una línea de comandos, en una URL ni en un archivo. Lo que el backend arranca no
  lo hereda: el entorno de los hijos del sandbox y aquel con el que corren `gh` y `git` descartan toda
  variable con nombre de secreto (salvo `GH_TOKEN` y las demás credenciales propias de gh).
- **Aprobar sin el código exige la pantalla.** Un token dice quién puede llamar a la API, no que el
  dueño esté mirando. Un sí sin código (`POST /api/approvals/{id}`) solo prospera cuando la petición
  lleva también la credencial de pantalla en `X-Chimera-Screen`, junto al bearer token. Se sortea en
  cada arranque y vive en memoria: nunca en un archivo, un registro, un log, el `.env` ni la
  respuesta de una ruta, nunca igual al bearer token, y se quita del entorno al leerla (el shell la
  pasa como `CHIMERA_SCREEN_TOKEN`). Cualquier token leído del disco — `service.token`, un
  `CHIMERA_SERVER_TOKEN` en el `.env` — sigue necesitando el código por pregunta para un sí; rechazar
  no necesita código. `chimera service` no tiene credencial de pantalla, así que ahí todo sí pide el
  código. `chimera app` en un navegador la pone en la página que sirve a una petición de loopback
  (`<meta name="chimera-screen">`), exactamente donde y cuando esa página ya lleva el bearer token —
  la misma exposición, por decisión del dueño del 16/10/2026.
- **El servicio en segundo plano.** `chimera service start` corre el mismo backend sin ventana.
  Publica `~/.chimera/service-<channel>.json` (0600, escrito de forma atómica; `CHIMERA_SERVICE_DIR`
  lo cambia de sitio) con el token persistente del dueño (`<home>/service.token`, o un
  `CHIMERA_SERVER_TOKEN` elegido por el dueño), y un cliente solo confía en ese archivo después de que
  `GET /api/info` responde con el mismo pid, versión, id y carpeta de datos. Ese token abre la API
  entera, así que se mantiene aparte del del puente: nada del lado MCP lee los archivos del servicio,
  y el puente los rechaza como archivos. El registro de arranque de la app instalada,
  `~/.chimera/desktop-install.json`, guarda solo cuatro variables de ruta — `CHIMERA_HOME`,
  `CHIMERA_WORKSPACE`, `CHIMERA_SHELL_PREFS`, `CHIMERA_SHELL_STATE` —, quien lo escribe rechaza
  cualquier otro nombre, y `start` lo valida antes de ejecutar nada a partir de él.

### Control total por el puente

El puente del escritorio (`chimera mcp desktop`) tiene tres interruptores, cada uno sin efecto sin el
anterior: **Permitir que Claude opere esta app**, **Control total** y, desde la decisión del dueño del
2026-10-18, **Control total por el puente**, en Ajustes > Seguridad.

- **Solo la pantalla lo activa.** `CHIMERA_BRIDGE_FULL_CONTROL` no está en la lista de ajustes editables,
  así que `PATCH /api/config` lo rechaza a cualquiera y el `settings.edit` del puente lo rechaza por su
  nombre. Su única ruta, `POST /api/security/bridge-full-control`, rechaza toda petición reenviada por el
  puente y solo lo activa con la credencial de pantalla; el token del dueño solo es `service.token`,
  legible en disco. La pantalla pide confirmación. Desactivarlo no necesita la pantalla: solo restringe.
- **Lo que añade.** Las decisiones del dueño (permitir comandos en una carpeta, guardar un agente con sus
  herramientas, reglas de aprobación recordadas, rechazar todo, recordar un proyecto, responder una
  sugerencia de ajuste, plugins y marketplaces, limpieza de worktrees), las rutas que exigen el
  `confirm: true` de la persona (restaurar, eliminar y exportar un diseño, las limpiezas de
  almacenamiento, reiniciar y restablecer), los ajustes del dueño — postura, protecciones, aprobaciones,
  límites, modelos y programación, escritos en vez de sugeridos — y abrir una pantalla de la app
  (`ui.navigate`, una dirección comprobada contra las pantallas de la propia app, nunca una URL).
- **Lo que nunca añade.** Credenciales: ninguna clave, token, contraseña o secreto OAuth se lee ni se
  escribe; el Runner, añadir un servidor MCP o iniciar sesión en él siguen siendo del dueño, y la bóveda
  de claves, la lectura del `.env` por el agente y la URL base del proveedor no se pueden escribir. Y
  quién llega a la app: vincular dispositivos, compartir, la puerta de la LAN, el token del servidor, los
  bots y sus listas de acceso.
- **El kernel decide primero.** Una aprobación responde a un REVIEW; un BLOCK del kernel nunca llega a
  quien aprueba. Las comprobaciones por ejecución siguen: una ejecución que inicia el puente usa los
  modelos y la postura configurados.
- **Visible y registrado.** Mientras está activo, cada pantalla muestra una barra con **Desactivar** en
  un clic, y cada llamada hecha con esa autoridad escribe una entrada de auditoría (`via: "bridge"`,
  `scope: "full_control"`, la ruta, el estado y los ids — nunca un cuerpo ni un valor), listada en la
  pestaña Seguridad.
- **Límite honesto.** El interruptor vive en `.env`, como los otros dos: un proceso que pueda escribir
  ese archivo lo activa sin la pantalla. Las herramientas del agente y el puente rechazan el archivo; el
  resto de lo que corre como el dueño, no.

## Lo que decide el dueño, y lo que un repositorio no puede

- **Política de despliegue, solo de denegación.** `<CHIMERA_HOME>/policy.json` (o el archivo que
  indique `CHIMERA_POLICY_FILE`) guarda reglas ordenadas `provider.use` / `integration.use` /
  `tool.use`; decide la última que coincide. Un `allow` ahí nunca concede nada: solo deshace un deny
  anterior del mismo archivo, así que no puede reabrir `CHIMERA_TOOL_DENYLIST`, el piso de alcance ni
  un BLOCK del kernel. Un archivo inválido nunca se aplica a medias: `serve`, `chat`, `assist`, `tui`
  y `acp` se niegan a arrancar, `doctor` falla, y el escritorio arranca pero rechaza toda herramienta
  hasta que se corrija. El agente no puede reescribirlo — las herramientas de escritura rechazan el
  archivo dondequiera que esté, y el puente no puede apuntarlo a otro sitio.
- **"Siempre en este proyecto".** En una tarjeta de aprobación el dueño puede recordar un sí para un
  proyecto: la herramienta, o, para un comando de shell, sus dos primeras palabras. Responde después
  las preguntas que coinciden, **incluso después de que la ejecución leyera contenido no confiable** —
  decisión del dueño del 15/10/2026, sabiendo que una página hostil podría dirigir un comando dentro
  de ese prefijo. Solo se salta la pregunta: un BLOCK del kernel y el piso de alcance siguen ganando,
  y un comando que encadena, usa pipe o redirige (`;`, `&&`, `|`, `>`, `$(`…) nunca coincide. Solo el
  dueño crea o revoca uno; el puente no puede, salvo que el dueño
  haya activado el control total por el puente (arriba).
- **Referencias con nombre.** Las carpetas que el dueño lista en `<CHIMERA_HOME>/references.json` son
  alcanzables como `@alias/path` solo por las herramientas de lectura (`read_file`, `list_dir`,
  `grep`, `glob`); toda herramienta de escritura rechaza un alias. Una raíz del sistema de archivos, el
  home del usuario o la carpeta de datos se rechazan como referencia. Solo las superficies atendidas
  las reciben — `chat`, `assist`, `tui`, un `solve` atendido, el turno Code del escritorio (nunca el
  de un invitado) —, así que bots, cron y `scenarios` no tienen ninguna.
- **Agentes en Markdown.** `.chimera/agents/*.md` en un repositorio es texto de otra persona, así que
  solo puede restringir: sus herramientas se intersecan con las herramientas gobernadas de la sesión,
  sus pasos quedan limitados por los de la sesión, las claves desconocidas se rechazan, su `model` se
  ignora, y su cuerpo se cerca como dato no confiable. Solo los archivos del dueño en
  `<CHIMERA_HOME>/agents` pueden fijar un modelo del catálogo.
- **Chats vinculados a conversaciones.** `/link` en Discord o Telegram hace que un chat continúe una
  conversación Code, y solo el dueño listado en la allowlist de ese bot puede vincular uno — sin
  allowlist, nadie. El texto de un desconocido en una sala vinculada nunca llega a la conversación, y
  los medios se rechazan en lugar de reenviarse. El turno corre sobre el piso del bot, no el del
  escritorio: solo los servidores MCP de `CHIMERA_BOT_MCP_SERVERS`, ningún conector OpenAPI, ni
  concesión de shell ni host exec.
- **Los conectores del dueño quedan fuera de las puertas que abre un desconocido.** Un conector
  OpenAPI llama hacia fuera con la clave del dueño, así que responde a quien conduzca la ejecución. No
  se carga en una superficie de mensajería mientras algún bot configurado no tenga allowlist, nunca
  en un job de issue de GitHub (cualquiera escribe la issue; solo se comprueba el repositorio), y en
  `/v1/chat/completions` solo mientras `CHIMERA_SERVER_TOKEN` esté definido. Un módulo,
  `chimera/governance/surfaces.py`, decide qué tipo de superficie es un nombre, por su familia
  (`platform:discord` es un bot); un test falla si un módulo vuelve a escribir su propia lista.

## Límites honestos

Esto mide si la acción dañina de un agente *ya inyectado* se detiene — no si el modelo puede ser
inyectado en primer lugar. El razonamiento libre sobre prosa no confiable, y la exfiltración a
través de herramientas legítimamente necesarias, siguen siendo problemas abiertos (rastreados
como [issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

La política completa y siempre actualizada vive en
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), incluyendo
cómo reportar una vulnerabilidad.

## Límites de la anulación de permisos de sesión

La anulación opcional de permisos de una sesión de código se autentica con una clave HMAC generada en memoria del proceso; esa clave nunca se escribe en disco ni se hereda en procesos secundarios. Se ignoran los indicadores `bypass_permissions` persistidos y las atestaciones `via_screen` en cola que no tengan un HMAC válido. Por tanto, la elección de permisos solo dura hasta que se reinicia el proceso de la aplicación/servicio; entonces la anulación queda desactivada por defecto. Un shell aún puede editar el JSON, pero no falsificar la atestación. Decisión del dueño (26/10): con el interruptor activado, el agente libera todo aquello por lo que de otro modo preguntaría: git y los push a GitHub, `gh`, las herramientas de conectores MCP, despliegues, cargas, escrituras HTTP y herramientas de envío, tanto en una ejecución limpia como en una contaminada. Tres cosas siguen preguntando al dueño, y nada más lo hace: (a) las reglas BLOCK del kernel y la denylist destructiva (`rm -rf /`, `mkfs`, ...); (b) enviar o leer un archivo secreto (`.env`, claves, `.npmrc`, `.netrc`, `.pypirc`, `.git-credentials`, credenciales de AWS) como parte de un comando saliente, y `-EncodedCommand` de PowerShell; (c) reescribir el historial de la rama por defecto en un remoto: un force push, un `+refspec` o el borrado de `main`/`master` (`git push --delete`, `git push origin :main`), incluido lo mismo escrito dentro de `bash -c` o `powershell -Command`, y los borrados con `gh api` de la referencia por defecto. Un force push a una rama de funcionalidad está liberado. Estas comprobaciones son heurísticas léxicas, no una frontera de seguridad: nombres expandidos por glob como `cat .e*`, cadenas ensambladas dinámicamente como `python -c "open('.'+'env')"`, codificaciones alternativas, envoltorios ni comandos fuera de la sintaxis reconocida no se pueden garantizar. Una página maliciosa aún puede eludirlas. Usa el sandbox aislado para trabajo no confiable.
