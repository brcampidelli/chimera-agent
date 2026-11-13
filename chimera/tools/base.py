"""The ``Tool`` abstraction.

A Tool is a single, well-described capability the agent can invoke (read a file,
run a shell command, fetch a URL, ...). Tools expose an OpenAI/LiteLLM-compatible
function schema so they can be advertised to any model through the gateway.

``name``, ``description`` and ``parameters`` are instance attributes: static tools
set them as class attributes, while *dynamically generated* tools (from an OpenAPI
spec or an MCP server) set them per instance in ``__init__``.

Skills (see :mod:`chimera.skills`) are higher-level, *learned* procedures that may
compose several tools; tools are the primitive, hand-written building blocks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Base class for a single agent capability."""

    name: str
    description: str
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    untrusted_output: bool = False
    """Whether this tool's result is external content that must be treated as untrusted.

    Declared here, on the interface, rather than being set ad-hoc on individual tools: the taint
    layer keys fencing, sanitisation and run-tainting off it, so a tool that carries it silently and
    a wrapper that silently drops it are the difference between a defended run and an undefended one.
    Wrappers must mirror it — see :func:`chimera.tools.base.is_untrusted_output`, which resolves it
    through a wrapper chain so a wrapper that forgets cannot quietly disarm the defence.
    """

    clips_own_output: bool = False
    """Whether this tool bounds its own result, so the registry's central ceiling leaves it alone.

    False by default: a tool that says nothing is held to ``clip.CENTRAL_MAX_OUTPUT_CHARS`` by
    :meth:`ToolRegistry.run`, so a new tool cannot hand the model an unbounded string by forgetting
    to cap it. True only for a tool that has a cap of its own (and so a marker of its own that says
    how to continue), or whose output is bounded by the registry that ran it. Resolved through the
    ``.inner`` chain, like ``untrusted_output``, so a wrapper that forgets cannot make the tool be
    cut twice, or not at all.
    """

    open_arguments: bool = False
    """Whether a name absent from ``parameters`` is still the tool's to judge.

    False for our own tools: their schema is the whole contract, and ``run`` reads only the names it
    knows, so anything else was silently dropped (see :func:`undeclared_arguments`). True for a proxy
    that forwards its arguments verbatim to a validator of its own — an MCP server — where JSON
    Schema's default applies and an unlisted name is allowed unless the schema says otherwise.
    Resolved through the ``.inner`` chain, like ``untrusted_output``, so a wrapper need not copy it.
    """

    @abstractmethod
    def run(self, **kwargs: Any) -> str:
        """Execute the tool and return a string result."""
        raise NotImplementedError

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the function-tool schema understood by LiteLLM/OpenAI APIs."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def is_untrusted_output(tool: Any) -> bool:
    """True if ``tool`` — or anything it wraps — produces untrusted external content.

    Wrappers (``GovernedTool``, ``LedgeredTool``, …) expose the inner tool as ``.inner``. Reading the
    marker off only the immediate inner meant that composing two wrappers dropped it: with
    ``--guard --taint`` the registry becomes ``LedgeredTool(GovernedTool(tool))``, the middle layer
    did not copy the flag, and document/media/file content stopped being fenced, stopped being
    sanitised, and stopped tainting the run — in the most protective-looking invocation available.
    Walking the chain makes the answer independent of wrapper order and of any future wrapper.
    """
    seen = 0
    current = tool
    while current is not None and seen < 16:  # bounded: a cycle must not hang the agent
        if bool(getattr(current, "untrusted_output", False)):
            return True
        current = getattr(current, "inner", None)
        seen += 1
    return False


def clips_own_output(tool: Any) -> bool:
    """True if ``tool`` — or anything it wraps — bounds its own output (see ``Tool.clips_own_output``)."""
    seen = 0
    current = tool
    while current is not None and seen < 16:  # bounded: a cycle must not hang the agent
        if bool(getattr(current, "clips_own_output", False)):
            return True
        current = getattr(current, "inner", None)
        seen += 1
    return False


def _chain_has_open_arguments(tool: Any) -> bool:
    seen = 0
    current = tool
    while current is not None and seen < 16:  # bounded, as in `is_untrusted_output`
        if bool(getattr(current, "open_arguments", False)):
            return True
        current = getattr(current, "inner", None)
        seen += 1
    return False


def undeclared_arguments(tool: Any, arguments: dict[str, Any]) -> list[str]:
    """The names in ``arguments`` that ``tool``'s schema does not declare, in call order.

    Empty when the schema is free-form by its own words: ``additionalProperties`` true or a schema,
    ``patternProperties``, or no (or an empty) ``properties`` list. With ``additionalProperties`` absent,
    our tools count as closed (their ``run`` ignores what it does not read) and a tool with
    ``open_arguments`` counts as open; an explicit ``additionalProperties: false`` closes either.

    Why it exists: on 2026-10-09 a model called ``read_file`` with ``offset`` for the declared
    ``start_line``. The tool took the unknown name without a word and returned the first window
    again; the model asked four more times and the turn hung. An ignored argument reads as a call
    that worked, which is the one answer a model cannot correct from.
    """
    schema = getattr(tool, "parameters", None)
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict) or schema.get("patternProperties"):
        return []
    extra = schema.get("additionalProperties")
    if extra is True or isinstance(extra, dict):
        return []
    if not properties and extra is not False:
        # `{"properties": {}}` is this class's default: what a tool has when it declared nothing,
        # not a promise that it takes nothing. Only an explicit `false` makes it a no-argument tool.
        return []
    if extra is not False and _chain_has_open_arguments(tool):
        return []
    return [name for name in arguments if name not in properties]


def unknown_arguments_error(tool: Any, arguments: dict[str, Any]) -> str | None:
    """The observation for a call with undeclared arguments, or None when every name is declared.

    Names the offending argument and lists the accepted ones, so the next call can be the right one.
    Deliberately not an alias (``offset`` → ``start_line``): that would hide the model's mistake
    from the model and from us, and keep the wrong name alive in every later call.
    """
    unknown = undeclared_arguments(tool, arguments)
    if not unknown:
        return None
    properties = getattr(tool, "parameters", {}).get("properties", {})
    accepted = ", ".join(properties) or "none"
    noun = "argument" if len(unknown) == 1 else "arguments"
    names = ", ".join(repr(n) for n in unknown)
    return f"error: {tool.name}: unknown {noun} {names}; accepted: {accepted}"


#: Marks an observation as a gate REFUSING to run the tool, rather than the tool having run.
#:
#: Here, on the leaf both the governance wrappers and the loop already import, because the question
#: "did this call do what it said" has been answered by string-sniffing in two places
#: (`Agent.run` and `ToolRegistry.run`) with the same rule written twice. A refusal that one of them
#: recognises and the other does not is the same class of bug as a wrapper that drops a taint
#: marker: the answer must not depend on who is asking.
_REFUSAL_MARK = "\u26d4"  # ⛔


class Refusal(str):
    """A refusal :func:`refusal` built: our own words, known by type rather than by their first
    character.

    The mark is enough for the loop, which only asks whether a call ran. It is not enough for the
    taint layer, which lets our refusal out of the data fence (``fence_observation``) and must never
    let a page out. A page, an MCP server or an email can begin with the same character, and text
    cannot tell the two apart. The type can: nothing that arrives from outside is built here.
    """

    __slots__ = ()


def refusal(text: str) -> Refusal:
    """Build a refusal observation. Every gate that declines to run a tool must go through here.

    The model reads a refusal outside the data fence, even from a fetch tool, so ``text`` must be
    ours: a reason, a rule's name, a fixed remedy. Never a fetched body or a remote error, which
    belong in an ``error:`` result, where the fence keeps them.
    """
    return Refusal(f"{_REFUSAL_MARK} {text}")


def tool_raised(name: str, exc: BaseException) -> str:
    """The observation for a tool that raised instead of answering.

    One sentence for every layer that catches: the loop (`Agent._run_tool`), and the taint layer for
    a tool whose exception can carry remote text (`LedgeredTool`, `MCPTool`), which then fences it.
    The model reads the same failure whoever caught it.
    """
    return f"error: tool {name!r} failed: {exc}"


def is_refusal(observation: str) -> bool:
    """True if a gate declined to run the tool, so nothing happened.

    NOT the same as an error, and deliberately NOT the same as `[idempotent: …]`. That one says the
    effect ALREADY HAPPENED and is not being repeated — reporting it as a refusal would claim the
    action did not occur, when it did. The difference matters most to whoever reads a receipt
    afterwards, which is the only reason any of this is being distinguished at all.
    """
    return observation.startswith(_REFUSAL_MARK)
