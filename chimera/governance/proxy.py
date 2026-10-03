"""Judge the tool a proxy runs, not the proxy.

``CHIMERA_DEFER_TOOLS`` puts every built-in outside files/search/shell behind ``tool_call(tool=…,
arguments=…)`` (`chimera.tools.defer`). Every governance check in this package decides by tool NAME
and by named arguments: the kernel's rules read the command half of :func:`render_action`, and the
taint ledger asks whether the name is in ``EXEC_TOOLS``, ``FETCH_TOOLS``, ``WRITE_TOOLS`` or a
narrowing set. Behind the proxy the name is ``tool_call`` — in none of them — and the program a
deferred ``execute_code`` runs travels inside ``arguments``, which the kernel files as a document.

Measured before this existed: ``execute_code(code="import os; os.system('rm -rf /')")`` is BLOCK
(rule ``rm_rf_root``) and the same call through ``tool_call`` was ALLOW; after an untrusted fetch,
``http_get`` with a query string is REVIEW and through ``tool_call`` was ALLOW. Turning on a switch
sold as "fewer schema tokens" switched the kernel and the ledger off for every tool it deferred.

So every check unwraps the call first, here, and judges ``(arguments["tool"], arguments["arguments"])``
— read exactly as :class:`~chimera.tools.defer.ToolCallTool` reads them, so what is judged is what
runs. The proxy's own result handling is unchanged: it stays ``untrusted_output`` and is fenced.

``mcp_call`` is NOT unwrapped here, deliberately and with a known cost. Its arguments are filed as a
document on purpose (``governed_tool._DOCUMENT_ARGS``): a server names its own parameters, and
unwrapping for the kernel would put ``api_token``-shaped values into the audit line. Server tool
names are in none of the built-in sets, so the kernel and ``assess_action`` would judge them the same
either way. The one check it does change is the ledger's unseen-recipient note, which matches a
server's ``*_send_email`` by suffix; through a deferred ``mcp_call`` that note does not fire. That
predates the built-in deferral and is left for its own change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, overload

#: The name :class:`chimera.tools.defer.ToolCallTool` registers under. Kept as a literal here rather
#: than imported, because governance must not import the tool layer it governs; a test pins the two.
DEFERRED_PROXY = "tool_call"

#: A proxy reached through a proxy is not something the catalogue offers (the proxies are registered
#: after it is built), so one level is all a real call has. The bound only keeps a crafted nesting
#: from looping; past it the call is judged at the depth reached.
_MAX_DEPTH = 4


@overload
def see_through(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]: ...
@overload
def see_through(name: str, args: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]: ...
def see_through(name: str, args: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    """``(name, args)`` of the tool that will actually run.

    Unchanged for every name except :data:`DEFERRED_PROXY`. For the proxy, the inner name is read
    with ``str(...).strip()`` and the inner arguments with ``or {}``, as the proxy does. When they
    are malformed (``arguments`` not an object) the proxy refuses without running anything, so the
    call is returned as written: there is nothing behind it to judge.

    The SAME objects are returned, never copies. The gates judge a mapping and then run with it, and
    `test_the_action_judged_is_the_action_executed` holds that what was judged is what ran by
    watching a gate that edits the dict it was shown reach execution; a copy here would make that
    check compare a dict nobody runs. Through the proxy, the inner mapping is the very object inside
    ``kwargs["arguments"]`` that the proxy hands its tool.
    """
    for _ in range(_MAX_DEPTH):
        if name != DEFERRED_PROXY:
            break
        inner = str(args.get("tool") or "").strip()
        inner_args = args.get("arguments") or {}
        if not inner or not isinstance(inner_args, dict):
            break
        name, args = inner, inner_args
    return name, args
