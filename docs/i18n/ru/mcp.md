---
source_sha256: 32a7d80a9e508e738b930dbf71e8edcc9a15e2366ad7c15ba6033a2ff5833b56
---

# Подключение серверов MCP

MCP (Model Context Protocol) — это стандартный способ подключить к агенту внешние инструменты: на
нём говорят GitHub, файловые системы, Notion, базы данных и ещё сотни серверов. У Chimera есть
полноценный клиент MCP: инструменты любого сервера становятся обычными инструментами Chimera, лежат в
том же реестре, что и встроенные, и подчиняются тем же слоям — списку разрешений, ядру и журналу.

## Установите дополнение с клиентом

Клиент MCP вынесен в необязательное дополнение, чтобы ядро оставалось лёгким:

```bash
uv sync --extra mcp
```

Большинство серверов — это пакеты Node, поэтому понадобится ещё `npx` (идёт вместе с Node.js).

## Проверка за 60 секунд (без учётных данных)

Эталонному серверу файловой системы не нужны никакие токены — он просто открывает инструменты чтения
и записи над выбранным вами каталогом:

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

Передайте этот реестр объекту `Agent` (или посмотрите `examples/mcp_github.py` с полным циклом) — и
модель сможет вызывать инструменты сервера как любые другие.

## Настоящий сервер: GitHub

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

Это и есть вся интеграция: в реестре появляются около 26 инструментов GitHub (искать репозитории,
читать файлы, перечислять задачи, создавать пул-реквесты и так далее). Готовый к запуску вариант:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## Как это ложится на слои безопасности

Инструменты MCP — это обычные объекты `Tool`, поэтому всё складывается:

- **Список разрешений на сессию** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  даёт только те инструменты MCP, которые нужны этому запуску; неразрешённые до модели не доходят.
- **Ядро управления** — `govern_registry(...)` пропускает вызовы MCP через разрешить / предупредить /
  разобрать / заблокировать так же, как любую команду оболочки.
- **Журнал заражения** — оберните через `ledger_registry(...)`, и обращения MCP будут записаны;
  учтите, что сегодня автоматически классифицируются только инструменты, названные в `FETCH_TOOLS`,
  поэтому считайте содержимое MCP недоверенным и предпочитайте режим `--taint --guard`, когда сервер
  тянет внешние данные.

## Chimera *в роли* сервера MCP

Клиент выше позволяет Chimera вызывать чужие инструменты. Обратное тоже работает: запустите Chimera
**как** сервер MCP, и любой клиент — Claude Desktop, среда разработки, другой агент — сможет вызвать
весь движок как три инструмента.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

Открываются:

| Инструмент | Что делает |
| --- | --- |
| `chimera_solve` | Самостоятельно решает задачу с планом и проверкой-или-откатом; возвращает ответ. |
| `chimera_fuse` | Отвечает на запрос через движок LLM-Fusion (панель → судья → синтезатор). |
| `chimera_memory_search` | Ищет в долговременной памяти Chimera и возвращает лучшие факты. |

Направьте на него клиент MCP как на сервер stdio. Для Claude Desktop добавьте в его конфигурацию:

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

Режиму `--mcp` нужен ключ поставщика для `chimera_solve` и `chimera_fuse` (поиск по памяти работает и
без него). Добавьте `--fuse`, чтобы направить глубокие ходы решателя через слияние, и `--no-memory`,
чтобы пропустить обращение к памяти. Поскольку проводом служит stdio, все журналы идут в stderr — в
stdout только протокол.

## Разговор на A2A (агент → агент)

MCP соединяет агентов с *инструментами*; **A2A** (Agent2Agent, Linux Foundation) соединяет агентов
*друг с другом* — он встроен в LangGraph, CrewAI и AutoGen. Chimera говорит и на нём, поэтому
оркестратор на LangGraph или CrewAI может передать задачу Chimera и получить готовый результат.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` добавляет к HTTP-серверу два маршрута:

| Маршрут | Назначение |
| --- | --- |
| `GET /.well-known/agent.json` | Карточка агента — кто он и какие навыки объявляет (solve, fuse). |
| `POST /a2a` | Жизненный цикл задачи по JSON-RPC 2.0: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

Клиент отправляет `message/send` с текстовой частью; Chimera запускает самостоятельного агента и
возвращает задачу в состоянии `completed` (или `failed`), несущую ответ как сообщение агента. Либо он
отправляет `message/stream` и получает поток **Server-Sent Events**: сначала задачу в состоянии
`working`, затем `completed` или `failed`, когда запуск завершится, — так оркестратор видит ход дела
без опроса. Карточка агента объявляет `capabilities.streaming: true`.

**Честно об охвате:** поток сейчас выдаёт два события (working → итог), а не пошаговые приращения
токенов, и push-уведомления не реализованы. Это соответствующий стандарту поток без нужды в опросе —
достаточно, чтобы быть полноценным потоковым узлом в приложении на LangGraph или CrewAI.

## Если что-то не работает

- `TimeoutError: MCP server ... did not become ready` — команда не запустилась. Выполните ту же строку
  `npx ...` вручную в терминале, чтобы увидеть её ошибку (нет токена, нет Node, медленно скачивается
  пакет при первом запуске — увеличьте `connect_timeout`).
- `ModuleNotFoundError: mcp` — установите дополнение: `uv sync --extra mcp`.
- Столкновения имён инструментов — всегда передавайте `name_prefix`.
- Сессия держит сервер подпроцессом всё время жизни вашего скрипта; вызовите `close()` у сессии
  `connector` (или просто дайте процессу завершиться), чтобы её свернуть.
