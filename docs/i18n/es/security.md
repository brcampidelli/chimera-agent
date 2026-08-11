---
source_sha256: cd4ba57b32db6a5d71c9c0c2452c9bdcba3b28ae416f06b2347ac14df0248b89
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

## Medido, no afirmado

```bash
chimera redteam
```

ejecuta un corpus de inyección a través del stack. En el corpus incorporado, la capa de taint
reduce la **tasa de éxito de ataque del 100% al ~14%** — y el informe *nombra* lo que aún se
filtra (exfiltración vía una herramienta permitida) en lugar de afirmar el 100%.

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
