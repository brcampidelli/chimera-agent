"""The tool schema each surface sends is kept as text, summed in the lint and described in a table.

The risk named in the plan is a snapshot that measures a registry no surface uses: it passes green and
protects nothing. So these tests pin the three things that would make it inert, and each one is proved
against the case that breaks it rather than against the case that works:

- a changed tool description moves the snapshot of exactly the surfaces that declare the tool;
- the text does not depend on the process, the environment or the machine's installed extras;
- the table and the lint total are the same numbers the snapshots hold.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from chimera.prompts import tool_schemas
from chimera.prompts.lint import tool_schema_totals
from chimera.prompts.tool_schemas import (
    DEFERRAL_APPLIES,
    NOT_MEASURED,
    SURFACES,
    TABLE_PATH,
    estimate_tokens,
    render_table,
    schema_snapshots,
)

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "tests" / "prompt_snapshots"


@pytest.fixture(scope="module")
def baseline() -> dict[str, str]:
    return schema_snapshots()


def _declares(text: str, tool: str) -> bool:
    return f'"name": "{tool}"' in text


def test_every_measured_surface_has_its_snapshot_on_disk(baseline: dict[str, str]) -> None:
    for surface in SURFACES:
        assert f"schema.{surface}" in baseline
        assert (SNAPSHOTS / f"schema.{surface}.txt").exists(), surface
        # The surfaces that defer also keep the undeferred text: a description edited on a tool
        # behind `tool_describe` is read by the model all the same and must move a snapshot.
        assert (f"schema.{surface}.full" in baseline) == (surface in DEFERRAL_APPLIES), surface


def test_the_deferral_each_surface_claims_is_in_its_own_text(baseline: dict[str, str]) -> None:
    """`DEFERRAL_APPLIES` was read from the source; the text is what proves it was read right."""
    for surface in SURFACES:
        default, _ = baseline[f"schema.{surface}"], None
        has_proxy = _declares(default, "tool_call")
        assert has_proxy == (surface in DEFERRAL_APPLIES), surface
    for surface in DEFERRAL_APPLIES:
        assert not _declares(baseline[f"schema.{surface}.full"], "tool_call"), surface


def test_a_core_tool_description_moves_every_surface_that_declares_it(
    monkeypatch: pytest.MonkeyPatch, baseline: dict[str, str]
) -> None:
    """The guard is not inert: change one description in memory and the comparison says so, on the
    surfaces that carry the tool and on no other."""
    from chimera.tools.files import ReadFileTool

    monkeypatch.setattr(ReadFileTool, "description", ReadFileTool.description + " EDITED-IN-MEMORY")
    changed = schema_snapshots()
    drifted = {name for name in baseline if changed[name] != baseline[name]}
    expected = {name for name, text in baseline.items() if _declares(text, "read_file")}
    assert expected, "no surface declares read_file: the test would prove nothing"
    assert drifted == expected
    assert all(f"schema.{surface}" in drifted for surface in SURFACES)


def test_a_deferred_tool_description_moves_only_what_can_show_it(
    monkeypatch: pytest.MonkeyPatch, baseline: dict[str, str]
) -> None:
    """`scrape` is behind `tool_describe` on the deferring surfaces. Its description is in the default
    text of the cron and the bot, and in the `.full` text of the rest, and NOT in `schema.code`."""
    from chimera.tools.scrape import ScrapeTool

    monkeypatch.setattr(ScrapeTool, "description", ScrapeTool.description + " EDITED-IN-MEMORY")
    changed = schema_snapshots()
    drifted = {name for name in baseline if changed[name] != baseline[name]}
    expected = {name for name, text in baseline.items() if _declares(text, "scrape")}
    assert drifted == expected
    assert "schema.cron" in drifted and "schema.bot" in drifted
    assert "schema.code.full" in drifted and "schema.cli.full" in drifted
    assert "schema.code" not in drifted and "schema.cli" not in drifted


def test_a_tool_only_one_surface_registers_moves_only_that_surface(
    monkeypatch: pytest.MonkeyPatch, baseline: dict[str, str]
) -> None:
    from chimera.tools.design import DesignTool

    monkeypatch.setattr(DesignTool, "description", DesignTool.description + " EDITED-IN-MEMORY")
    changed = schema_snapshots()
    drifted = {name for name in baseline if changed[name] != baseline[name]}
    assert drifted and drifted <= {"schema.design", "schema.design.full"}


def test_the_machine_and_the_environment_do_not_reach_the_text(
    monkeypatch: pytest.MonkeyPatch, baseline: dict[str, str]
) -> None:
    """A maintainer's key, a `CHIMERA_*` switch and a policy variable are all set, and the text is
    the same: the surfaces are assembled hermetically, not from whatever the process holds."""
    monkeypatch.setenv("CHIMERA_DEFER_TOOLS", "false")
    monkeypatch.setenv("CHIMERA_EDIT_BATCH", "true")
    monkeypatch.setenv("CHIMERA_TODO_LIST", "false")
    monkeypatch.setenv("TAVILY_API_KEY", "t" "vly-not-a-real-key")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.invalid")
    assert schema_snapshots() == baseline


def test_assembling_a_surface_leaves_the_process_as_it_found_it() -> None:
    from chimera.config import get_settings

    # Settings first: building them exports the `.env` credentials into the environment, which is
    # the caller's state the snapshot has to include, not something `assemble` did.
    before_settings = get_settings()
    before_env = dict(os.environ)
    before_spec = importlib.util.find_spec
    tool_schemas.assemble("cli", defer_tools=True)
    assert dict(os.environ) == before_env
    assert importlib.util.find_spec is before_spec
    assert get_settings() == before_settings  # a fresh object: the cache was cleared on the way out


def test_two_processes_produce_the_same_bytes(tmp_path: Path, baseline: dict[str, str]) -> None:
    """Different hash seeds, different working folders, different temporary roots: no dict order, temp
    path, date or environment key may end up in the text."""
    program = (
        "import hashlib, json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from chimera.prompts.tool_schemas import schema_snapshots\n"
        "print(json.dumps({k: hashlib.sha256(v.encode()).hexdigest() for k, v in schema_snapshots().items()}))\n"
    )
    digests = []
    for seed, workdir in (("1", tmp_path / "a"), ("2", tmp_path / "b")):
        workdir.mkdir()
        env = {**os.environ, "PYTHONHASHSEED": seed, "TEMP": str(workdir), "TMP": str(workdir), "TMPDIR": str(workdir)}
        done = subprocess.run(
            [sys.executable, "-c", program, str(ROOT)],
            capture_output=True, text=True, cwd=workdir, env=env, check=True, timeout=240,
        )
        digests.append(json.loads(done.stdout.strip().splitlines()[-1]))
    assert digests[0] == digests[1]
    here = {k: hashlib.sha256(v.encode()).hexdigest() for k, v in baseline.items()}
    assert digests[0] == here


def test_the_text_holds_no_path_of_this_machine(baseline: dict[str, str]) -> None:
    for name, text in baseline.items():
        for fragment in (str(ROOT), str(Path.home()), "AppData", "tmp"):
            assert fragment not in text, f"{name} carries {fragment!r}"


def test_the_table_is_current() -> None:
    assert TABLE_PATH.read_text(encoding="utf-8") == render_table(), (
        "docs/tool-schema-by-surface.md is stale: run python scripts/tool_schema_table.py"
    )


def test_the_table_names_every_surface_with_a_number_or_a_reason() -> None:
    table = TABLE_PATH.read_text(encoding="utf-8")
    for surface in SURFACES:
        assert tool_schemas.LABELS[surface] in table, surface
    for surface, reason in NOT_MEASURED.items():
        assert surface in table and reason.strip() and reason in table
    assert "characters divided by four" in table and "estimate" in table


def test_the_table_numbers_are_the_snapshot_numbers(baseline: dict[str, str]) -> None:
    """The table's character counts are those of the compact JSON of the snapshot text itself."""
    table = TABLE_PATH.read_text(encoding="utf-8")
    for surface in SURFACES:
        default = json.dumps(json.loads(baseline[f"schema.{surface}"]), ensure_ascii=False, separators=(", ", ": "))
        assert f"| {len(default):,} |" in table, surface


def test_the_lint_totals_every_surface_and_says_its_tokens_are_an_estimate() -> None:
    rows = {row["surface"]: row for row in tool_schema_totals()}
    for surface in SURFACES:
        row = rows[surface]
        assert row["measured"] is True
        assert row["chars_defer_off"] >= row["chars"] > 0
        assert row["tokens_estimate"] == estimate_tokens(row["chars"])
        assert row["tokens_estimate_defer_off"] == estimate_tokens(row["chars_defer_off"])
        assert "estimate" in row["tokens_are"] and "est." in row["detail"]
        assert (row["chars"] < row["chars_defer_off"]) == (surface in DEFERRAL_APPLIES), surface
    for surface, reason in NOT_MEASURED.items():
        assert rows[surface]["measured"] is False and reason in rows[surface]["detail"]


def _load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_lint_script_prints_the_section_and_json_carries_the_same_rows(
    capsys: pytest.CaptureFixture[str],
) -> None:
    script = _load_script("prompt_lint")
    assert script.main([]) == 0  # it reports and never fails
    text = capsys.readouterr().out
    assert "tool schema by surface" in text and "characters / 4" in text
    assert script.main(["--json"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    schema_lines = [line for line in lines if line["rule"].startswith("tool schema")]
    assert {line["section"] for line in schema_lines} == {*SURFACES, *NOT_MEASURED}
    for line in schema_lines:
        assert line["detail"] in text, line["section"]


def test_the_table_script_check_fails_on_a_stale_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    stale = tmp_path / "table.md"
    stale.write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(tool_schemas, "TABLE_PATH", stale)
    script = _load_script("tool_schema_table")
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert script.main(["--check"]) == 1
    assert stale.read_text(encoding="utf-8") == "stale\n"
    assert script.main([]) == 0 and stale.read_text(encoding="utf-8") == render_table()


@pytest.mark.parametrize("tool", ["ask_user", "suggest_task", "dismiss_task"])
def test_the_code_turn_declares_the_screen_tools_and_follows_their_description(
    monkeypatch: pytest.MonkeyPatch, baseline: dict[str, str], tool: str
) -> None:
    """The Code surface is the attended turn: a registry without the tools that turn registers would
    leave their descriptions outside every snapshot."""
    from chimera.tools.ask_user import AskUserTool
    from chimera.tools.task_suggestion import DismissTaskTool, SuggestTaskTool

    cls = {"ask_user": AskUserTool, "suggest_task": SuggestTaskTool, "dismiss_task": DismissTaskTool}[tool]
    for surface in ("code", "design"):
        assert _declares(baseline[f"schema.{surface}"], tool) or _declares(
            baseline[f"schema.{surface}.full"], tool
        ), (surface, tool)
    monkeypatch.setattr(cls, "description", cls.description + " EDITED-IN-MEMORY")
    changed = schema_snapshots()
    drifted = {name for name in baseline if changed[name] != baseline[name]}
    expected = {name for name, text in baseline.items() if _declares(text, tool)}
    assert expected and drifted == expected
