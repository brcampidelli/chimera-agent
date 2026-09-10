---
source_sha256: eeb0e80877d9cd939362a1d6f1b736437c3918f1b24f1fb1b44e18f31aa71e42
---

# Seguridad y salvaguardas

Chimera puede ejecutar comandos de shell, editar archivos, llamar APIs, y modificar sus propias
skills. Viene con **defensa en profundidad**, y — esto importa — la documentación indica dónde
*se detiene* cada capa.

!!! warning "La única regla"
    Ninguna de estas salvaguardas reemplaza **ejecutarlo en un entorno aislado** cuando otorgas
    autonomía. El runner `local` por defecto no está aislado; usa
    `CHIMERA_SANDBOX=docker` (sin red, opcionalmente bajo gVisor) para trabajo no confiable.

## Las capas

- **Kernel de gobernanza** — cada llamada a herramienta gobernada es allow / warn / review /
  block. Un primer filtro barato de firmas de shell peligrosas, no la frontera.
- **Sandbox** — un contenedor efímero, sin red (`CHIMERA_SANDBOX=docker`), endurecible con
  gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`).
  El **comando verify** corre en el mismo sandbox que la shell del agente, y cuando ese sandbox no
  está aislado un comando que no escribiste tú — inferido del repositorio, leído de un trabajo
  cron, de una tarjeta o de un workflow — pasa por la misma confirmación `CHIMERA_HOST_EXEC`; si
  la rechazas, se abstiene en lugar de correr (`CHIMERA_VERIFY_NETWORK=1` le da red a un
  verificador docker; en los sandboxes de kernel no puede, así que un verificador que necesita red
  corre en el host, y solo cuando lo escribiste tú).
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

## Límites honestos

Esto mide si la acción dañina de un agente *ya inyectado* se detiene — no si el modelo puede ser
inyectado en primer lugar. El razonamiento libre sobre prosa no confiable, y la exfiltración a
través de herramientas legítimamente necesarias, siguen siendo problemas abiertos (rastreados
como [issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

La política completa y siempre actualizada vive en
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), incluyendo
cómo reportar una vulnerabilidad.
