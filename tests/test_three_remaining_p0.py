"""The last three from the scope audit: a fence three commands never got, and two files that
stopped the product instead of losing one record.

Each is the shape the whole week has been: a rule written down in one place, and a neighbour that
never received it.

1. `chat`, `assist` and `tui` built their tool registry raw. `run` applies the deployment fence one
   line after building the same registry; the desktop app applies it too. These three were the only
   agent surfaces without it — and the `project_root` comment sitting in those exact lines was added
   a day earlier, for the sibling field, without anyone noticing the missing fence beside it.

2. `MemoryStore.load` had a per-record guard whose comment says "one bad entry must not lose every
   other memory" — and the `json.loads` above it was outside that guard. A file truncated mid-write
   is not a list with one bad entry; it is not JSON. Every command builds a memory manager at boot,
   so one truncated file stopped the product rather than one memory.

3. `save_playbook` was a bare `write_text` running after EVERY `solve`, and the playbook is one JSON
   array — so a death mid-write cost every bullet, not the last one. `load_playbook` then raised on
   the result, and it is called before any work starts.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from chimera.config import Settings
from chimera.memory.models import MemoryItem
from chimera.memory.store import MemoryStore

MAIN = pathlib.Path(__file__).resolve().parents[1] / "chimera" / "cli" / "main.py"

#: Agent surfaces that must apply the deployment fence. Not "everything that builds a registry":
#: `tools` prints a table and the benches assemble their own — naming them keeps the check honest
#: about what it is asserting.
FENCED_COMMANDS = ("chat", "assist", "tui", "agent")


#: Where a command may delegate its assembly to. One hop, and only into functions this test can
#: read, so "the fence is applied somewhere" never becomes "the fence is applied, probably".
BUILDERS = (MAIN, MAIN.parent / "right_hand.py")


def _applies_fence(node: ast.AST) -> bool:
    return any(
        isinstance(inner, ast.Call) and getattr(inner.func, "id", "") == "_apply_tool_allowlist"
        for inner in ast.walk(node)
    )


def _top_level(name: str) -> ast.AST | None:
    for path in BUILDERS:
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
                return node
    return None


def _fence_reaches(command: str) -> bool:
    """True when ``command`` passes its registry through `_apply_tool_allowlist` — itself, or in a
    function it calls.

    An AST walk rather than a grep: a grep cannot tell the call from the word appearing in the
    comment that explains it, and this file exists because of a comment nobody read closely.

    The one-hop half arrived with `chimera/cli/right_hand.py`. `chat` and `assist` stopped naming
    the fence in their own bodies when their whole assembly moved into `build_right_hand` — which
    applies it in the same position, between `default_registry` and the wrappers. A body-local walk
    then reported those two as unfenced, which was a true statement about where the call sits and a
    false one about the guarantee this test exists to hold. Resolution is by NAME against
    module-level functions only: an attribute call (`obj.method(...)`) is not followed, because a
    name collision would make this answer yes about a surface that does nothing of the kind.
    """
    body = _top_level(command)
    if body is None:
        raise AssertionError(f"no command named {command!r} — retarget this test")
    if _applies_fence(body):
        return True
    called = {
        node.func.id
        for node in ast.walk(body)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return any(
        (helper := _top_level(name)) is not None and _applies_fence(helper)
        for name in sorted(called - {command})
    )


@pytest.mark.parametrize("command", FENCED_COMMANDS)
def test_every_agent_surface_applies_the_deployment_fence(command: str) -> None:
    """`run` did this from the start. The other three did not, and nothing said so.

    The fence is deliberately NOT `governed_profile` here: these are attended surfaces, and the
    kernel and taint ledger are the inferential half that can refuse legitimate work. An allowlist
    is an instruction — it removes a named tool and cannot be wrong about intent — so it needs no
    rollout and no attendance argument. That distinction is the one #83 settled.
    """
    assert _fence_reaches(command), (
        f"`{command}` builds its tools without _apply_tool_allowlist — an owner who fenced their "
        "agent in .env gets no fence on this surface"
    )


def test_the_check_can_still_see_a_missing_fence() -> None:
    """Proof it is not vacuous: the same walk over a command that does not apply it."""
    tree = ast.parse("def demo():\n    registry = default_registry(ws)\n")
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))

    assert not _applies_fence(node)


def test_the_one_hop_does_not_credit_a_command_for_a_call_it_never_makes() -> None:
    """The looseness the hop introduced, bounded.

    Following a delegate is what keeps this check honest now that `chat` calls `build_right_hand`
    instead of the fence directly. It is also how such a check goes vacuous: resolve loosely enough
    and every command "reaches" the fence through something. So the hop is one level, by name, into
    module-level functions only — and a command that calls something unrelated must still fail.
    """
    assert _fence_reaches("chat"), "the delegate is no longer followed"
    assert _applies_fence(_top_level("build_right_hand")), "the delegate stopped applying the fence"
    assert not _fence_reaches("pet_status"), "a command that assembles nothing reads as fenced"


# --- a truncated file loses one record, not the product -------------------------------------------


def test_a_truncated_memory_file_does_not_stop_every_command(tmp_path: pathlib.Path) -> None:
    """The guard below the parse says one bad entry must not lose every other memory. The parse
    itself was above it, so a half-written file raised out of the constructor — and chat, solve,
    serve, the app and every API request build one at boot."""
    path = tmp_path / "memory.json"
    path.write_text('[{"id": "a", "content": "kept"}, {"id": "b", "cont', encoding="utf-8")

    store = MemoryStore(path)  # must not raise

    assert store.all() == []


def test_a_readable_file_still_loads_every_record(tmp_path: pathlib.Path) -> None:
    """The direction that matters more: degrading on corruption must not degrade on health."""
    path = tmp_path / "memory.json"
    seed = MemoryStore(path)
    seed.add(MemoryItem(id="a", content="one"))
    seed.add(MemoryItem(id="b", content="two"))

    assert {item.id for item in MemoryStore(path).all()} == {"a", "b"}


def test_a_memory_file_holding_the_wrong_shape_is_ignored(tmp_path: pathlib.Path) -> None:
    """Valid JSON, wrong type — a hand edit that wrapped the array in an object. Iterating a dict
    would silently walk its KEYS and validate strings as memories."""
    path = tmp_path / "memory.json"
    path.write_text('{"items": []}', encoding="utf-8")

    assert MemoryStore(path).all() == []


def test_the_store_recovers_on_the_next_write(tmp_path: pathlib.Path) -> None:
    """Degrading to empty is only acceptable if the next write repairs the file — otherwise the
    agent quietly runs memoryless forever."""
    path = tmp_path / "memory.json"
    path.write_text("{ truncated", encoding="utf-8")

    store = MemoryStore(path)
    store.add(MemoryItem(id="new", content="after the corruption"))

    assert {item.id for item in MemoryStore(path).all()} == {"new"}


# --- the playbook, written after every solve ------------------------------------------------------


def _settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path))


def test_a_corrupt_playbook_does_not_stop_the_next_run(tmp_path: pathlib.Path) -> None:
    """`build_evolution_context` loads this before any work starts, so raising here does not lose
    the playbook — it stops the run. For advisory content that is the wrong trade."""
    from chimera.evolution.wiring import load_playbook, playbook_path

    playbook_path(_settings(tmp_path)).write_text('{"bullets": [{"id": "x"', encoding="utf-8")

    assert load_playbook(_settings(tmp_path)).to_dict() is not None  # must not raise


def test_the_playbook_file_is_never_opened_for_truncation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mid-write crash cannot be injected portably, but the property that makes it impossible can:
    the live file is only ever renamed onto, never opened in a mode that empties it first.

    Both `io.open` and `builtins.open` are watched — pathlib reaches `io.open` by its own reference,
    and a version of this test that watched only the second passed against the code it exists to
    catch.
    """
    import io

    from chimera.evolution.playbook import Playbook
    from chimera.evolution.wiring import playbook_path, save_playbook

    settings = _settings(tmp_path)
    save_playbook(settings, Playbook())
    target = playbook_path(settings)
    opened: list[tuple[str, str]] = []
    real_open = io.open

    def watch(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        opened.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(io, "open", watch)
    monkeypatch.setattr("builtins.open", watch)
    save_playbook(settings, Playbook())
    monkeypatch.undo()

    truncating = [
        (name, mode)
        for name, mode in opened
        if pathlib.Path(name) == target and ("w" in mode or "+" in mode)
    ]
    assert not truncating, f"the live playbook was opened in a truncating mode: {truncating}"


def test_a_playbook_round_trips(tmp_path: pathlib.Path) -> None:
    """The fix must not trade a crash for silent data loss — the whole point is that it still saves."""
    from chimera.evolution.playbook import Playbook
    from chimera.evolution.wiring import load_playbook, save_playbook

    settings = _settings(tmp_path)
    original = Playbook()
    save_playbook(settings, original)

    assert load_playbook(settings).to_dict() == original.to_dict()
