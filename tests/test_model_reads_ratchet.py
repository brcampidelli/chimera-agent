"""Reads of a Settings model field outside ``config.py`` and ``routing.py`` can only fall in number.

Sixteen ``Settings`` fields name a model (``default_model``, ``weak_model`` ... ``voice_work_model``).
``Settings.routing_table()`` now answers which model each role runs on and why, and the point of
moving consumers onto it is that the question stops being asked in eighty places, each with its own
idea of the fallback. This pins the count so that moving one is visible, and adding a new read of a
model field in the middle of the app (instead of asking the table) is a reviewed decision.

The count is by AST, not by text: an attribute LOAD whose name is one of the fields, and a
``getattr(x, "<field>")`` with a literal name. A store (``self.mid_model = ...``) is not a read.

A count of LITERAL reads is blind to a read that does not spell the name, so every way of reaching a
field without spelling it is pinned too, in ``DYNAMIC_ALLOWED``: ``getattr`` with a computed name,
``getattr`` handed on under another name (``read = getattr``), ``operator.attrgetter``, ``vars()``,
``__dict__``, ``__getattribute__``, ``model_dump()`` and a field name used as a dict key. The scan
cannot tell a Settings object from any other, so the existing ones are pinned as found (none was
audited one by one in slice (a)); a NEW one fails until it is read through the routing table or
listed with a reason a reviewer accepts.
The starting point, counted on ``main`` at 0.90.0, was 97 reads, 9 of which are not Settings at all
(``NOT_SETTINGS``) and 88 are (``KEPT``). A later slice that migrates a consumer lowers a number in
``KEPT``; one that adds a read raises it, which needs a reason a reviewer accepts.
Slice (b) moved ``providers/privacy.py``, the models block of ``GET /api/config`` and ``chimera models``
onto the table: 25 reads gone, 63 of Settings left.
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from chimera.config import Settings

ROOT = Path(__file__).resolve().parents[1]
EXEMPT_FILES = frozenset({"chimera/config.py", "chimera/providers/routing.py"})
FIELDS = frozenset(name for name in Settings.model_fields if name.endswith("_model"))

#: (file, field) -> (reads, why this is NOT a read of a Settings field). Homonyms: another class has
#: an attribute of the same name. They are listed rather than skipped so that a new one is noticed.
NOT_SETTINGS: dict[tuple[str, str], tuple[int, str]] = {
    ("chimera/orchestration/hierarchy.py", "mid_model"): (
        4, "`self.mid_model`: the orchestrator's own constructor argument, which the caller resolved",
    ),
    ("chimera/cli/commands/setup.py", "default_model"): (
        3, "`ProviderInfo.default_model`: the catalogue's suggestion for a provider the wizard shows",
    ),
    ("chimera/cli/commands/learning.py", "default_model"): (
        1, "`result.default_model` of a migration report, not a setting",
    ),
    ("chimera/api/config_api.py", "default_model"): (
        1, "`known[env].default_model`: `ProviderInfo` again, for the providers list",
    ),
}

_RESOLVER = "A resolver the routing table DELEGATES to (it decides today); it moves only when the table replaces it"
_SCREEN = "The Settings screen and the setup wizard show these fields; slice (b) moved the models block and `chimera models` onto the table, what is left is read elsewhere on the screen"
_MODALITY = "A media/voice tool reading its own model; migrates when the table has consumers"
_CONSUMER = "A consumer that reads the model field directly; migrates onto `routing_table()` in a later slice"

#: (file, field) -> (reads, why it stays). Lowering a number or deleting an entry is the point.
KEPT: dict[tuple[str, str], tuple[int, str]] = {
    ("chimera/api/app.py", "complete_model"): (1, _CONSUMER),
    ("chimera/api/app.py", "decision_model"): (2, _CONSUMER),
    ("chimera/api/app.py", "default_model"): (2, _CONSUMER),
    ("chimera/api/code_api.py", "default_model"): (3, _CONSUMER),
    ("chimera/api/code_api.py", "voice_model"): (1, _CONSUMER),
    ("chimera/api/code_api.py", "voice_work_model"): (2, _CONSUMER),
    ("chimera/api/commands_api.py", "default_model"): (1, _CONSUMER),
    ("chimera/api/config_api.py", "chat_voice_model"): (1, _SCREEN),
    ("chimera/api/config_api.py", "complete_model"): (4, _SCREEN),
    ("chimera/api/config_api.py", "decision_model"): (2, _SCREEN),
    ("chimera/api/config_api.py", "default_model"): (3, _SCREEN),
    ("chimera/api/config_test.py", "default_model"): (1, _CONSUMER),
    ("chimera/api/desktop_bridge.py", "default_model"): (1, _CONSUMER),
    ("chimera/api/side_chat.py", "default_model"): (1, _CONSUMER),
    ("chimera/cli/commands/bench.py", "default_model"): (1, _CONSUMER),
    ("chimera/cli/commands/bench.py", "embed_model"): (2, _CONSUMER),
    ("chimera/cli/commands/chat.py", "default_model"): (1, _CONSUMER),
    ("chimera/cli/commands/misc.py", "embed_model"): (3, _CONSUMER),
    ("chimera/cli/commands/serve.py", "default_model"): (1, _CONSUMER),
    ("chimera/cli/commands/setup.py", "default_model"): (3, _SCREEN),
    ("chimera/cli/decisions_cmd.py", "decision_model"): (1, _CONSUMER),
    ("chimera/cli/review_cmd.py", "default_model"): (1, _CONSUMER),
    ("chimera/core/agent.py", "default_model"): (1, _CONSUMER),
    ("chimera/core/escalation.py", "default_model"): (1, _RESOLVER),
    ("chimera/decisions/factory.py", "decision_model"): (1, _RESOLVER),
    ("chimera/design/propose.py", "default_model"): (1, _CONSUMER),
    ("chimera/evolution/wiring.py", "embed_model"): (1, _CONSUMER),
    ("chimera/fusion/verified.py", "decision_model"): (1, _RESOLVER),
    ("chimera/fusion/verified.py", "verified_answers_escalate_model"): (1, _RESOLVER),
    ("chimera/providers/catalog.py", "default_model"): (2, _RESOLVER),
    ("chimera/providers/catalog.py", "mid_model"): (2, _RESOLVER),
    ("chimera/providers/catalog.py", "orchestrator_model"): (2, _RESOLVER),
    ("chimera/providers/catalog.py", "weak_model"): (2, _RESOLVER),
    ("chimera/providers/gateway.py", "default_model"): (2, _CONSUMER),
    ("chimera/providers/gateway.py", "embed_model"): (1, _CONSUMER),
    ("chimera/review/reviewer_for.py", "review_model"): (1, _RESOLVER),
    ("chimera/server/voice.py", "chat_voice_model"): (1, _MODALITY),
    ("chimera/tools/media.py", "audio_model"): (1, _MODALITY),
    ("chimera/tools/media.py", "image_model"): (1, _MODALITY),
    ("chimera/tools/media.py", "tts_model"): (1, _MODALITY),
    ("chimera/tools/media.py", "video_model"): (1, _MODALITY),
}

_BASELINE = "Found when the ratchet was written (slice (a)); not audited site by site. "
_GETATTR = _BASELINE + "A computed attribute name on some object; none was shown to read a Settings model field"
_DUMP = _BASELINE + "A pydantic model dumped to a dict; the scan cannot tell Settings from another model"
_DICT = _BASELINE + "`vars()` / `__dict__` on some object"
_KEYED = _BASELINE + "A model-field name used as a dict key (a request body or a report, not a Settings read)"

#: (file, way) -> (count, why). Same rule as KEPT: it may fall, and it never rises without a reason.
DYNAMIC_ALLOWED: dict[tuple[str, str], tuple[int, str]] = {
    ("chimera/api/__init__.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/api/app.py", "dyn:__dict__"): (1, _DICT),
    ("chimera/api/app.py", "dyn:model_dump"): (10, _DUMP),
    ("chimera/api/code_api.py", "dyn:__dict__"): (2, _DICT),
    ("chimera/api/code_api.py", "dyn:model_dump"): (5, _DUMP),
    ("chimera/api/code_api.py", "dyn:vars"): (1, _DICT),
    ("chimera/api/commands_api.py", "dyn:model_dump"): (8, _DUMP),
    ("chimera/api/config_api.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/api/connectors_api.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/api/features.py", "dyn:model_dump"): (9, _DUMP),
    ("chimera/api/manual_gate.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/api/orchestration_api.py", "dyn:model_dump"): (4, _DUMP),
    ("chimera/api/orchestration_api.py", "dyn:vars"): (1, _DICT),
    ("chimera/api/roles.py", "dyn:model_dump"): (2, _DUMP),
    ("chimera/api/storage_api.py", "dyn:keyed"): (1, _KEYED),
    ("chimera/api/turn_context.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/cli/commands/_serve_teardown.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/cli/commands/serve.py", "dyn:__dict__"): (1, _DICT),
    ("chimera/core/__init__.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/core/agent.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/core/autonomous.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/core/claude_agent_format.py", "dyn:getattr-nonliteral"): (2, _GETATTR),
    ("chimera/core/markdown_agents.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/core/registry.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/core/service_record.py", "dyn:getattr-nonliteral"): (2, _GETATTR),
    ("chimera/decisions/gate.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/deployment_policy.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/design/model.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/design/propose.py", "dyn:__dict__"): (2, _DICT),
    ("chimera/eval/__init__.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/eval/scenarios.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/evolution/auto_evolve.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/evolution/experience.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/features.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/fusion/cascade.py", "dyn:getattr-nonliteral"): (2, _GETATTR),
    ("chimera/fusion/consistency.py", "dyn:getattr-nonliteral"): (2, _GETATTR),
    ("chimera/fusion/route_log.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/fusion/router.py", "dyn:getattr-nonliteral"): (2, _GETATTR),
    ("chimera/governance/__init__.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/governance/approval.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/governance/bypass.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/governance/ledger.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/governance/mcp_readonly.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/governance/quarantine.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/integrations/mcp_config.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/integrations/mcp_config.py", "dyn:model_dump"): (2, _DUMP),
    ("chimera/integrations/mcp_pins.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/kanban/board.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/memory/store.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/orchestration/budget.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/orchestration/metering.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/prompts/registry.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/providers/failover.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/providers/gateway.py", "dyn:getattr-nonliteral"): (3, _GETATTR),
    ("chimera/providers/gateway.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/providers/privacy.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/scheduler/store.py", "dyn:model_dump"): (1, _DUMP),
    ("chimera/server/allowlist.py", "dyn:getattr-nonliteral"): (2, _GETATTR),
    ("chimera/server/manager.py", "dyn:getattr-nonliteral"): (2, _GETATTR),
    ("chimera/skills/skill_md.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/tools/__init__.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/tools/conditional.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/tools/design.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
    ("chimera/tools/web.py", "dyn:getattr-nonliteral"): (1, _GETATTR),
}


def _scan_source(source: str) -> Iterator[tuple[str, str]]:
    """``("read", field)`` for a literal read, ``("dyn:<way>", "")`` for a way of reading that hides
    the name. A store (``self.mid_model = ...``) is neither."""
    tree = ast.parse(source)
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if isinstance(node.ctx, ast.Load) and node.attr in FIELDS:
                yield "read", node.attr
            way = {
                "__dict__": "dyn:__dict__", "__getattribute__": "dyn:__getattribute__",
                "attrgetter": "dyn:attrgetter", "getattr": "dyn:getattr-alias",
                "model_dump": "dyn:model_dump", "vars": "dyn:vars",
            }.get(node.attr)
            if way:
                yield way, ""
        elif isinstance(node, ast.Name):
            if node.id == "getattr" and id(node) not in called:
                yield "dyn:getattr-alias", ""  # `read = getattr`, or handed on as an argument
            elif node.id == "attrgetter":
                yield "dyn:attrgetter", ""
            elif node.id == "__builtins__":
                yield "dyn:getattr-alias", ""
            elif node.id == "vars":
                yield "dyn:vars", ""
        elif isinstance(node, ast.ImportFrom):
            # `from builtins import getattr as read` and `from operator import attrgetter` hand the
            # reader on under a name the Name check below never sees; any alias counts.
            for alias in node.names:
                way = {"getattr": "dyn:getattr-alias", "attrgetter": "dyn:attrgetter", "vars": "dyn:vars"}.get(alias.name)
                if way:
                    yield way, ""
        elif isinstance(node, ast.Call):
            yield from _scan_call(node)
        elif isinstance(node, ast.Subscript):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in FIELDS:
                yield "dyn:keyed", ""


def _scan_call(node: ast.Call) -> Iterator[tuple[str, str]]:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) >= 2:
        name = node.args[1]
        if isinstance(name, ast.Constant) and isinstance(name.value, str):
            if name.value in FIELDS:
                yield "read", name.value
        else:
            yield "dyn:getattr-nonliteral", ""
    elif (
        isinstance(func, ast.Attribute)
        and func.attr in {"get", "pop", "setdefault"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value in FIELDS
    ):
        yield "dyn:keyed", ""


def _tree() -> tuple[Counter[tuple[str, str]], Counter[tuple[str, str]]]:
    reads: Counter[tuple[str, str]] = Counter()
    dynamic: Counter[tuple[str, str]] = Counter()
    for path in sorted((ROOT / "chimera").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in EXEMPT_FILES:
            continue
        for kind, name in _scan_source(path.read_text(encoding="utf-8")):
            if kind == "read":
                reads[(rel, name)] += 1
            else:
                dynamic[(rel, kind)] += 1
    return reads, dynamic


def _kinds(source: str) -> list[str]:
    return sorted(kind for kind, _name in _scan_source(source))


def test_the_model_fields_are_the_sixteen_the_audit_counted() -> None:
    assert len(FIELDS) == 16, sorted(FIELDS)


def test_reads_of_model_fields_are_pinned_and_can_only_fall() -> None:
    found, _ = _tree()
    allowed = Counter({key: n for key, (n, _why) in KEPT.items()})
    allowed.update({key: n for key, (n, _why) in NOT_SETTINGS.items()})
    risen = {key: (found[key], allowed[key]) for key in found if found[key] > allowed[key]}
    assert not risen, (
        f"new reads of a model field outside config.py/routing.py: {risen}. "
        "Ask `settings.routing_table()`, or add a reason to KEPT."
    )
    fallen = {key: (found[key], allowed[key]) for key in allowed if found[key] < allowed[key]}
    assert not fallen, (
        f"reads went away: {fallen}. Lower the number in KEPT (or delete the entry) - "
        "the ratchet only means something while it is tight."
    )


def test_dynamic_access_is_pinned_and_can_only_fall() -> None:
    _, found = _tree()
    allowed = Counter({key: n for key, (n, _why) in DYNAMIC_ALLOWED.items()})
    risen = {key: (found[key], allowed[key]) for key in found if found[key] > allowed[key]}
    assert not risen, (
        f"a new way of reading an attribute without naming it: {risen}. A model field read this way "
        "is invisible to the count above. Read it through `settings.routing_table()`, spell the name, "
        "or list it in DYNAMIC_ALLOWED with why it is not a Settings model field."
    )
    fallen = {key: (found[key], allowed[key]) for key in allowed if found[key] < allowed[key]}
    assert not fallen, f"dynamic accesses went away: {fallen}. Lower the number in DYNAMIC_ALLOWED."


def test_the_two_ways_around_the_literal_count_are_caught() -> None:
    # Both reads below leave the literal count unchanged; the scan has to see them anyway.
    computed = 'field = "default_model"\nvalue = getattr(settings, field)\n'
    aliased = 'read = getattr\nvalue = read(settings, "default_model")\n'
    assert _kinds(computed) == ["dyn:getattr-nonliteral"]
    assert "dyn:getattr-alias" in _kinds(aliased)


def test_importing_the_readers_under_another_name_is_caught() -> None:
    # The remaining evasion of the second review: the name never appears as `getattr` again.
    imported = "from builtins import getattr as read\nvalue = read(settings, field)\n"
    assert "dyn:getattr-alias" in _kinds(imported)
    assert "dyn:getattr-alias" in _kinds("from builtins import getattr\n")
    assert "dyn:vars" in _kinds("from builtins import vars as v\n")
    assert "dyn:attrgetter" in _kinds("from operator import attrgetter as ag\n")
    assert "dyn:attrgetter" in _kinds("import operator\nf = operator.attrgetter('default_model')\n")


def test_the_other_dynamic_ways_are_caught_and_plain_code_is_not() -> None:
    assert "dyn:attrgetter" in _kinds("import operator\nf = operator.attrgetter('default_model')\n")
    assert "dyn:attrgetter" in _kinds("from operator import attrgetter\n")
    assert "dyn:vars" in _kinds("d = vars(settings)\n")
    assert "dyn:__dict__" in _kinds("d = settings.__dict__\n")
    assert "dyn:__getattribute__" in _kinds("settings.__getattribute__(name)\n")
    assert "dyn:model_dump" in _kinds('x = settings.model_dump()["weak_model"]\n')
    assert "dyn:keyed" in _kinds('x = payload["default_model"]\n')
    assert "dyn:keyed" in _kinds('x = payload.get("weak_model")\n')
    assert "dyn:getattr-alias" in _kinds("import builtins\nx = builtins.getattr\n")
    assert "dyn:getattr-alias" in _kinds("import builtins as b\nx = b.getattr\n")
    assert "dyn:vars" in _kinds("import builtins\nx = builtins.vars(settings)\n")
    assert "dyn:getattr-alias" in _kinds("x = __builtins__['getattr']\n")
    # A literal read is a read, a store is nothing, and a literal getattr of another name is nothing.
    assert _kinds("x = settings.default_model\n") == ["read"]
    assert _kinds('x = getattr(settings, "default_model", "")\n') == ["read"]
    assert _kinds("self.mid_model = 1\n") == []
    assert _kinds('x = getattr(obj, "something_else")\n') == []


def test_every_entry_has_a_reason() -> None:
    assert all(why.strip() for _n, why in KEPT.values())
    assert all(why.strip() for _n, why in NOT_SETTINGS.values())
    assert all(why.strip() for _n, why in DYNAMIC_ALLOWED.values())


def test_the_count_it_started_from() -> None:
    # 97 raw reads on main at 0.90.0: 9 homonyms + 88 of Settings. Slice (b) took it to 63. This
    # number may fall, never rise.
    assert sum(n for n, _ in NOT_SETTINGS.values()) == 9
    assert sum(n for n, _ in KEPT.values()) <= 63
