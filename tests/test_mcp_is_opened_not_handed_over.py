"""Every MCP server's full schema was re-sent on every step of every turn.

A handful of servers is a few thousand tokens on a call that may not touch any of them; a catalogue
is a standing tax on the whole session. The comparable product that measured this reported **−46.9%
of total agent tokens** after serving MCP as something the agent opens rather than something it is
handed. Three tools replace N: `mcp_list` to see what exists, `mcp_describe` to read one schema,
`mcp_call` to run it.

**Off by default, and the tests below say why rather than assume it away.** The saving is in tokens;
the risk is in selection accuracy, because a model that has to search for a tool may choose worse
than one handed the list. Nothing here measures that half. `describe_saving` measures the first half
on the machine that will pay for it, because the −46.9% was measured on somebody else's harness with
somebody else's servers.

The two properties that are **not** optional are asserted hardest: denial is re-applied inside the
proxy, and the proxy is marked as producing untrusted output.
"""

from __future__ import annotations

import json
from typing import Any

from chimera.integrations.mcp_defer import (
    McpCallTool,
    describe_saving,
    register_deferred_mcp,
)
from chimera.tools.base import Tool, is_untrusted_output
from chimera.tools.registry import ToolRegistry


class _McpTool(Tool):
    """Stands in for a server-published tool: a real name, a real schema, a real result."""

    def __init__(self, name: str, description: str = "does a thing") -> None:
        self.name = name
        self.description = description
        self.parameters = {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Which city to look up, in full."},
                "days": {"type": "integer", "description": "How many days ahead, 1 to 14."},
            },
            "required": ["city"],
        }
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return f"{self.name} ran with {sorted(kwargs)}"


class _Pool:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = tools

    def all_tools(self) -> list[Tool]:
        return list(self._tools)


def _registry(pool: _Pool, **kwargs: Any) -> ToolRegistry:
    reg = ToolRegistry()
    register_deferred_mcp(pool, reg, **kwargs)
    return reg


# --------------------------------------------------------------------------- the shape


def test_three_tools_replace_however_many_there_are() -> None:
    pool = _Pool([_McpTool(f"srv__tool_{i}") for i in range(25)])

    reg = _registry(pool)

    assert set(reg.names()) == {"mcp_list", "mcp_describe", "mcp_call"}


def test_nothing_is_registered_when_there_is_nothing_to_defer() -> None:
    """Three tools that can only answer "nothing here" are three tools' worth of schema for
    nothing — which is the tax this exists to remove."""
    assert _registry(_Pool([])).names() == []


def test_the_catalogue_is_reachable_and_the_schema_is_not_until_asked() -> None:
    pool = _Pool([_McpTool("srv__get_weather", "Look up the weather for a city.")])
    reg = _registry(pool)

    listed = reg.run("mcp_list")
    assert "srv__get_weather" in listed
    assert "Look up the weather" in listed
    # The catalogue is for choosing. The argument schema is the expensive half and stays behind
    # mcp_describe — if it leaked into the listing there would be no saving.
    assert "days" not in listed

    described = json.loads(reg.run("mcp_describe", tool="srv__get_weather"))
    assert described["parameters"]["required"] == ["city"]


def test_a_long_catalogue_says_it_was_cut() -> None:
    """Truncating in silence reads as a complete answer, and the agent concludes the tool it needs
    does not exist."""
    reg = _registry(_Pool([_McpTool(f"srv__t{i:03d}") for i in range(90)]))

    listed = reg.run("mcp_list")

    assert "more; narrow the query" in listed


def test_calling_reaches_the_real_tool() -> None:
    target = _McpTool("srv__get_weather")
    reg = _registry(_Pool([target]))

    out = reg.run("mcp_call", tool="srv__get_weather", arguments={"city": "Recife", "days": 2})

    assert target.calls == [{"city": "Recife", "days": 2}]
    assert "srv__get_weather ran" in out


def test_arguments_sent_as_a_json_string_still_work() -> None:
    """Models send this field as a string often enough that refusing it would spend a turn on a
    formatting round-trip rather than on the task."""
    target = _McpTool("srv__get_weather")
    reg = _registry(_Pool([target]))

    reg.run("mcp_call", tool="srv__get_weather", arguments='{"city": "Olinda"}')

    assert target.calls == [{"city": "Olinda"}]


# --------------------------------------------------------------------------- the two that matter


def test_a_denied_tool_is_unreachable_through_the_proxy() -> None:
    """The hole deferral opens if nobody closes it: the restriction filter matches registry names,
    and after deferral the server's names are not in the registry. A denylist would silently stop
    removing the tool while one proxy could still reach it."""
    target = _McpTool("srv__danger")
    reg = _registry(_Pool([target, _McpTool("srv__safe")]), denied=frozenset({"srv__danger"}))

    assert "srv__danger" not in reg.run("mcp_list")
    assert "srv__safe" in reg.run("mcp_list")
    out = reg.run("mcp_call", tool="srv__danger", arguments={})
    assert out.startswith("error:")
    assert target.calls == [], "a denied tool ran anyway"


def test_a_deployment_allowlist_is_a_ceiling_here_too() -> None:
    kept, dropped = _McpTool("srv__allowed"), _McpTool("srv__other")
    reg = _registry(_Pool([kept, dropped]), allowed=frozenset({"srv__allowed"}))

    assert "srv__other" not in reg.run("mcp_list")
    reg.run("mcp_call", tool="srv__other", arguments={})
    assert dropped.calls == []


def test_a_refusal_does_not_reveal_whether_the_name_exists() -> None:
    """Denied and absent answer identically. Distinguishing them would make the proxy an oracle for
    what an owner's denylist contains — a fact a run reading untrusted content has no business
    being able to probe."""
    # A permitted tool alongside the denied one, because a pool where EVERYTHING is denied registers
    # nothing at all — three tools that can only answer "nothing here" are three tools' worth of
    # schema for nothing. The first draft of this test denied the only tool and then asked the proxy
    # a question, which is a state the product deliberately does not produce.
    reg = _registry(
        _Pool([_McpTool("srv__secret"), _McpTool("srv__ordinary")]),
        denied=frozenset({"srv__secret"}),
    )

    denied = reg.run("mcp_call", tool="srv__secret", arguments={})
    absent = reg.run("mcp_call", tool="srv__never_existed", arguments={})

    assert denied.replace("srv__secret", "X") == absent.replace("srv__never_existed", "X")


def test_the_proxy_declares_that_its_output_is_untrusted() -> None:
    """The taint layer keys fencing and run-tainting off this flag, and it is read before anyone
    knows which tool the proxy will call — so mirroring the target's flag would resolve, at the
    moment it matters, to "unknown". True unconditionally is the only safe reading."""
    assert McpCallTool.untrusted_output is True
    assert is_untrusted_output(_registry(_Pool([_McpTool("srv__x")])).get("mcp_call"))


# --------------------------------------------------------------------------- the measurement


def test_the_saving_is_measured_here_not_quoted_from_elsewhere() -> None:
    """The −46.9% that motivates this was measured on another harness with other servers. The only
    number that says anything about your session is the one taken from your own `mcp.json`."""
    pool = _Pool([_McpTool(f"srv__tool_{i}") for i in range(20)])

    saving = describe_saving(pool)

    assert saving["tools"] == 20
    assert saving["deferred_chars"] < saving["declared_chars"] / 2, (
        "twenty servers' schemas did not cost more than three fixed ones — either the measurement "
        "is wrong or there is nothing here worth deferring"
    )


def test_deferral_is_off_until_somebody_chooses_it() -> None:
    """Named as a choice rather than left as a default. Turning it on by conviction is what this
    project has rules against, and the accuracy half is unmeasured."""
    from chimera.config import Settings

    assert Settings.model_fields["mcp_defer"].default is False
