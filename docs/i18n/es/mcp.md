---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
---

# Conectando servidores MCP

MCP (Model Context Protocol) es la forma estándar de conectar herramientas externas a un
agente — GitHub, sistemas de archivos, Notion, bases de datos, y cientos de servidores más lo
hablan. Chimera tiene un cliente MCP de primera clase: las herramientas de cualquier servidor se
convierten en tools normales de Chimera, sentadas en el mismo registro que las incorporadas,
gobernadas por las mismas capas de lista blanca/kernel/ledger.

## Instalar el extra del cliente

El cliente MCP vive detrás de un extra opcional para que el núcleo se mantenga ligero:

```bash
uv sync --extra mcp
```

La mayoría de los servidores son paquetes de Node, así que también necesitas `npx` (viene con
Node.js).

## Prueba de humo de 60 segundos (sin credenciales)

El servidor de referencia de sistema de archivos no necesita ningún token — simplemente expone
herramientas de lectura/escritura sobre un directorio que elijas:

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

Entrega ese registro a un `Agent` (o consulta `examples/mcp_github.py` para el bucle completo) y
el modelo ya puede llamar a las herramientas del servidor como a cualquier otra.

## Un servidor real: GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

Esa es toda la integración: ~26 herramientas de GitHub (buscar repos, leer archivos, listar
issues, crear PRs, ...) aparecen en el registro. Versión ejecutable de extremo a extremo:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## Cómo encaja con las capas de seguridad

Las herramientas MCP son objetos `Tool` ordinarios, así que todo se compone:

- **Lista blanca por sesión** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  otorga solo las herramientas MCP que esta ejecución necesita; las no otorgadas nunca llegan al
  modelo.
- **Kernel de gobernanza** — `govern_registry(...)` controla las llamadas MCP con
  allow/warn/review/block igual que cualquier comando de shell.
- **Ledger de taint** — envuelve con `ledger_registry(...)` y los fetches de MCP quedan
  registrados; ten en cuenta que hoy solo las herramientas nombradas en `FETCH_TOOLS` se
  clasifican automáticamente, así que trata el contenido MCP como no confiable y prefiere
  ejecutar con la semántica `--taint --guard` cuando el servidor extrae datos externos.

## Chimera *como* servidor MCP

El cliente anterior permite que Chimera llame a otras herramientas. Lo inverso también funciona:
ejecuta Chimera **como** un servidor MCP para que cualquier cliente MCP — Claude Desktop, un
IDE, otro agente — pueda llamar a todo el motor como tres herramientas.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

Expone:

| Tool | Qué hace |
| --- | --- |
| `chimera_solve` | Resuelve una tarea de forma autónoma con plan + verify-or-revert; devuelve la respuesta. |
| `chimera_fuse` | Responde un prompt a través del motor LLM-Fusion (panel → juez → sintetizador). |
| `chimera_memory_search` | Busca en la memoria de largo plazo de Chimera y devuelve los hechos principales. |

Apunta un cliente MCP hacia él como un servidor stdio. Para Claude Desktop, agrega a su
configuración:

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` necesita una clave de proveedor para `chimera_solve`/`chimera_fuse` (la búsqueda en
memoria funciona sin una). Agrega `--fuse` para enrutar los turnos profundos del solver a través
de la fusión, `--no-memory` para omitir el recall. Como stdio es el canal, todos los logs van a
stderr — stdout lleva solo el protocolo.

## Hablando A2A (agente → agente)

MCP conecta agentes con *herramientas*; **A2A** (Agent2Agent, Linux Foundation) conecta agentes
entre *sí* — es nativo en LangGraph, CrewAI, y AutoGen. Chimera también lo habla, así que un
orquestador de LangGraph/CrewAI puede delegar una tarea a Chimera y recibir de vuelta un
resultado completado.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` agrega dos rutas al servidor HTTP:

| Ruta | Propósito |
| --- | --- |
| `GET /.well-known/agent.json` | La Agent Card — identidad + habilidades anunciadas (solve, fuse). |
| `POST /a2a` | Ciclo de vida de tarea JSON-RPC 2.0: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

Un cliente envía `message/send` con una parte de texto; Chimera ejecuta el agente autónomo y
devuelve una tarea `completed` (o `failed`) que lleva la respuesta como un mensaje de agente. O
envía `message/stream` y obtiene un stream de **Server-Sent Events**: primero la tarea en estado
`working`, luego la tarea `completed`/`failed` una vez que la ejecución termina — así un
orquestador ve el progreso sin hacer polling. La agent card anuncia
`capabilities.streaming: true`.

**Alcance, con honestidad:** actualmente el stream emite dos eventos (working → final), no
deltas de token por paso, y las notificaciones push no están implementadas. Eso es un stream
conforme, libre de polling — suficiente para ser un nodo de streaming de primera clase en una
app de LangGraph/CrewAI.

## Solución de problemas

- `TimeoutError: MCP server ... did not become ready` — el comando no arrancó. Ejecuta la misma
  línea `npx ...` manualmente en una terminal para ver su error (token faltante, Node faltante,
  descarga lenta del paquete en el primer uso — aumenta `connect_timeout`).
- `ModuleNotFoundError: mcp` — instala el extra: `uv sync --extra mcp`.
- Choques de nombres de herramienta — siempre pasa un `name_prefix`.
- La sesión ejecuta el servidor como un subproceso durante la vida de tu script; llama al
  `close()` de la sesión del `connector` (o simplemente deja que el proceso termine) para
  desmontarlo.
