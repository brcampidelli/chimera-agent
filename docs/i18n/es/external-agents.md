---
source_sha256: 91c3e19b5cb75bc83cafd3408047b7f1df85f8cb28d2be4b66897ce7a2e6a1ba
---

# Agentes externos (ACP)

Chimera puede entregar un turno de código a un agente que no escribió — Claude Code, Gemini CLI, o
cualquier adaptador que hable el [Agent Client Protocol](https://agentclientprotocol.com). La
transcripción, el verificador, la copia de seguridad y el deshacer siguen siendo de Chimera; el
trabajo es de otro.

## Por qué

La tesis de Chimera nunca fue que su bucle sea el único bueno. Es la gobernanza alrededor de un
bucle: el registro de contaminación, la región de escritura, la copia antes del turno, el veredicto
después, el recibo que dice lo que realmente pasó. Eso vale para cualquier ejecutor. Negarse a
dirigir uno en el que ya confías sería insistir en la mitad menos interesante del producto.

## Qué está garantizado y qué no

Lee esta parte antes de la instalación, porque es la que decide si esta función te sirve.

Un agente ACP declara qué capacidades del cliente usará, y Chimera ofrece `fs/read_text_file` y
`fs/write_text_file`. **Ofrecer no es imponer.** Los agentes que vale la pena dirigir tienen sus
propias herramientas de archivos y de terminal: Claude Code escribe a través del Claude Agent SDK, y
no tiene ninguna obligación de preguntarnos antes.

En concreto:

| | Bucle propio de Chimera | Agente externo |
|---|---|---|
| La región de escritura rechaza fuera de ella | Siempre | Solo lo que pasa por nosotros |
| El shell corre en el sandbox configurado | Siempre | El agente ejecuta a su manera |
| El registro de contaminación arma la puerta | Siempre | Solo en las herramientas que mediamos |
| Copia del workspace antes del turno | Sí | **Sí** |
| Deshacer el turno completo con un clic | Sí | **Sí** |
| Cada permiso concedido aparece en el recibo | — | **Sí** |

Las tres últimas filas son la garantía real, y son lo que promete la línea de postura de la pantalla
de Código cuando hay un agente externo seleccionado. Deja de decir "edita dentro de `/proyecto`, no
ejecuta comandos" — esa frase describe herramientas que Chimera controla — y dice en su lugar que se
tomó una copia y que el turno puede deshacerse. Una pantalla que mantuviera la frase más fuerte
estaría haciendo una promesa que el turno no puede cumplir.

Chimera también **rechaza** la capacidad de terminal de ACP. Un terminal alojado por nosotros sería
una segunda vía de ejecución junto al sandbox, sin ninguna de sus reglas.

## Instalación

Nada que configurar para los agentes que Chimera conoce:

```bash
npm i -g @agentclientprotocol/claude-agent-acp   # Claude Code, necesita Node 22+
npm i -g @google/gemini-cli                       # Gemini CLI (su modo ACP es experimental en origen)
```

Luego comprueba qué puede ejecutar realmente esta máquina:

```bash
chimera doctor
```

`external_agents` informa de cada uno con `available: true/false` y, cuando es falso, la línea que lo
soluciona. La disponibilidad se resuelve en la máquina donde corre el sidecar — que, en una compilación
de escritorio empaquetada, es una máquina montada por CI que nadie miró. Es decir: "debería estar
ahí" no es evidencia.

La aplicación de escritorio muestra una fila **Quién ejecuta** encima del compositor con lo que
encontró `doctor`. Cuando no hay nada ejecutable instalado, la fila no aparece; `doctor` es el lugar
para "todavía no tienes esto, y así se instala".

## Credenciales

Todo proceso hijo que Chimera lanza recibe un entorno sin las variables `API_KEY` / `TOKEN` /
`SECRET`, para que un comando de shell no pueda mostrar una clave de proveedor. Un agente ACP es un
programa cuyo trabajo entero necesita una, así que cada agente declara **por nombre** las variables
que necesita, y solo esas se devuelven:

- Claude Code: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`
- Gemini CLI: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`

Pasar el entorno completo sería más fácil y le daría a cada futuro adaptador todas las claves de la
máquina.

## Un adaptador propio

Codex y otros llegan a ACP mediante adaptadores de terceros que este proyecto no ha ejecutado. En vez
de listar un comando sin verificar — lo que convertiría "no lo comprobamos" en "compatible" — apunta
Chimera al que tú tengas:

```jsonc
// POST /api/code/turn
{
  "message": "arregla el test que falla",
  "provider": "custom",
  "provider_command": "npx -y algun-adaptador-acp --flag"
}
```

El comando se divide al estilo shell y se ejecuta **sin** shell, así que una tubería suelta es un
argumento y no un segundo comando. En Windows, un argumento con sintaxis de cmd.exe (`& | < > ^ %`)
que llegue a un lanzador `.cmd` se rechaza en lugar de escaparse: las reglas de comillas cambian
entre lanzadores, y un mal cálculo ejecuta tu máquina en vez de un programa en ella.

## Cómo funciona

- Un proceso hijo por **conversación**, no por turno. Un `session/prompt` es un mensaje dentro de un
  contexto que el agente mantiene; un proceso nuevo cada vez haría de cada turno el turno uno.
- Como máximo cuatro vivos a la vez, y uno sin tocar durante una hora se cierra. Cada uno es un
  proceso que sostiene una conexión con el modelo.
- El proceso nace en su propio grupo y se mata como árbol — un agente de código es un lanzador, y
  matar solo el proceso que sostenemos dejaría los trabajadores corriendo y la carpeta bloqueada. Un
  reaper en `atexit` cubre el caso de cerrar la app a mitad de un turno.
- Las notificaciones `session/update` del agente se traducen a los mismos eventos que emite el bucle
  nativo, así que la pantalla no necesita una segunda implementación. Los fragmentos de razonamiento
  se descartan en vez de mezclarse con la respuesta; un bloque `diff` se convierte en el parche
  unificado que la transcripción ya muestra.
- Los números que el bucle nativo posee y este no — `steps`, `context_peak_tokens` — llegan como
  `null` y no como `0`. Cero se leería como "no hizo nada".

## Límites

- Las peticiones de permiso se responden con `allow_once` y se **registran en el recibo**. Bloquear
  una petición que el agente no estaba obligado a hacer es teatro; la versión honesta es conceder,
  registrar y apoyarse en la copia de seguridad — que también cubre las escrituras que nunca
  preguntaron.
- La fusión, los roles, la memoria y el mapa del repositorio son del bucle propio de Chimera. Un
  turno externo informa `fused: false` y ningún uso de memoria porque nada de eso ocurrió.
- El modo ACP de Gemini está marcado como experimental en origen y su comportamiento puede cambiar
  entre versiones.
