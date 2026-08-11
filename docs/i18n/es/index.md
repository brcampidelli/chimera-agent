---
source_sha256: fe1348e242b1851c75bb1525ecf723afe068c499ed372335aca5e281cc72ba04
---

# Chimera

Un agente de IA de código abierto (Apache-2.0) y autoevolutivo cuyo núcleo de razonamiento
**fusiona varios modelos** (panel → juez → sintetizador) detrás de un enrutador consciente del
costo — con un kernel de gobernanza, un sandbox, y una memoria que aprende.

Este sitio está orientado a tareas: elige lo que quieres hacer.

<div class="grid cards" markdown>

- **:material-rocket-launch: Empieza**
  Instala, agrega una clave, ejecuta tu primera tarea en cinco minutos.
  [Instalación y primera ejecución →](usage.md)

- **:material-toolbox: Haz algo real**
  Recipes ejecutables: triaje de correo, un resumen diario de investigación, un vigilante de
  repos.
  [Recipes →](recipes.md)

- **:material-power-plug: Conecta herramientas**
  Conecta cualquier servidor MCP (GitHub, sistema de archivos, …).
  [Servidores MCP →](mcp.md)

- **:material-server: Ponlo a operar**
  Ejecútalo 24/7 en un servidor pequeño; programa trabajos; entrega a chat.
  [Despliegue →](deploy.md)

- **:material-shield-lock: Seguridad**
  Gobernanza, sandbox, seguimiento de taint — y sus límites honestos.
  [Seguridad →](security.md)

- **:material-sitemap: Entiéndelo**
  Cómo encajan el núcleo de fusión, la evolución, y las capas de seguridad.
  [Arquitectura →](architecture.md)

</div>

## La línea de un solo comando

```bash
uv sync --extra dev && uv run chimera init
```

Luego prueba `chimera run "..."`, o una recipe real:

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## Honesto por defecto

Chimera está en **alpha**. Viene con defensa en profundidad, pero la documentación dice
claramente dónde se detiene cada salvaguarda — las defensas contra inyección incluso publican un
número medido (`chimera redteam`). Consulta [Seguridad](security.md).
