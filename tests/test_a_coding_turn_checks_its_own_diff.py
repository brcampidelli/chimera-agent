"""A coding turn checks its own diff before its answer is final (`chimera/core/diff_self_check.py`).

The rules are deterministic and the integrity rule is reused, not copied: these tests hold the cases to
stable kinds, a clean diff to silence, an unmeasured diff to never-clean, and the Code turn to the
`self_check` field on `done` and to the note on the STORED answer. Nothing here touches the network or
a real git repository; the end-to-end tests write into a plain temporary folder with a fake agent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.checkpoint import FileSnapshot, TurnChange
from chimera.core.context_budget import RunState
from chimera.core.diff_self_check import (
    CRLF_INTRODUCED,
    GENERATED_WITHOUT_SOURCE,
    MAX_NAME_TOKENS,
    SCRATCH_FILE,
    UNNAMED_FILE,
    ChangedFile,
    changed_files_from,
    check_turn_diff,
    could_have_changed,
    incomplete_reason_of,
    named_paths_of,
)
from chimera.interface import ChatSession

WIDGET_BEFORE = "def widget():\n    return 4\n"
WIDGET_TEST_BEFORE = (
    "def test_widget_returns_four():\n"
    "    assert widget() == 4\n"
    "\n"
    "\n"
    "def test_widget_is_positive():\n"
    "    assert widget() > 0\n"
)
WIDGET_TEST_AFTER = "def test_widget_returns_four():\n    assert widget()\n"
NAMED = ["src/widget.py"]


def edit(path: str, before: str | None, after: str | None) -> ChangedFile:
    return ChangedFile(
        path=path,
        existed_before=before is not None,
        exists_after=after is not None,
        before=before,
        after=after,
    )


def kinds(check: Any) -> set[str]:
    return {f.kind for f in check.findings}


# --- the five cases, each flagged under a stable kind ---------------------------------------------

def test_one_diff_with_each_of_the_five_cases_flags_each_of_them() -> None:
    changes = [
        edit("src/widget.py", WIDGET_BEFORE, "def widget():\r\n    return 4\r\n"),  # CRLF introduced
        edit("tests/test_widget.py", WIDGET_TEST_BEFORE, WIDGET_TEST_AFTER),  # a test removed, one weakened
        edit("docs/notes.md", None, "a note nobody asked for\n"),  # unnamed
        edit("run-output.txt", None, "ok\n"),  # scratch at the root, created by the turn
        edit("apps/desktop/openapi.json", "{}\n", '{"openapi": "3.1"}\n'),  # generated, no generator input moved
    ]

    check = check_turn_diff(changes, named_paths=NAMED)

    assert not check.clean
    by_path = {(f.kind, f.path) for f in check.findings}
    assert (UNNAMED_FILE, "docs/notes.md") in by_path
    assert (SCRATCH_FILE, "run-output.txt") in by_path
    assert (GENERATED_WITHOUT_SOURCE, "apps/desktop/openapi.json") in by_path
    assert (CRLF_INTRODUCED, "src/widget.py") in by_path
    assert ("tests_removed_or_skipped", "tests/test_widget.py") in by_path
    # The named source file is part of the request, and the test of it is not outside the request.
    assert all(f.path != "src/widget.py" or f.kind == CRLF_INTRODUCED for f in check.findings)
    assert all(f.path != "tests/test_widget.py" or f.kind != UNNAMED_FILE for f in check.findings)


def test_the_payload_and_the_note_carry_the_same_findings() -> None:
    check = check_turn_diff([edit("run-output.txt", None, "x\n")], named_paths=NAMED)

    assert check.to_payload() == {
        "findings": [{"kind": SCRATCH_FILE, "path": "run-output.txt",
                      "detail": "left at the repository root; delete it or move it"}],
        "clean": False,
        "measured": True,
        "measured_reason": "",
    }
    note = check.note()
    assert note is not None and note.startswith("Self-check of this turn's own changes")
    assert "- scratch_file: run-output.txt" in note


# --- silence: a clean, measured diff says nothing ---------------------------------------------------

def test_a_clean_diff_has_no_findings_and_no_note() -> None:
    check = check_turn_diff([edit("src/widget.py", WIDGET_BEFORE, "def widget():\n    return 5\n")], named_paths=NAMED)

    assert check.clean and check.findings == ()
    assert check.note() is None
    assert check.to_payload() == {"findings": [], "clean": True, "measured": True, "measured_reason": ""}


def test_no_changes_is_clean_whatever_the_request_names() -> None:
    assert check_turn_diff([], named_paths=NAMED).clean
    assert check_turn_diff([], named_paths=None).to_payload()["clean"] is True


def test_a_test_of_a_named_file_is_not_outside_the_request() -> None:
    changes = [
        edit("src/widget.py", WIDGET_BEFORE, "def widget():\n    return 5\n"),
        edit("tests/test_widget.py", None, "def test_new():\n    assert widget() == 5\n"),
    ]

    assert kinds(check_turn_diff(changes, named_paths=NAMED)) == {"tests_touched"}


def test_the_request_gets_no_unnamed_check_when_it_names_no_file() -> None:
    changes = [edit("docs/notes.md", None, "a note\n")]

    assert kinds(check_turn_diff(changes, named_paths=None)) == set()
    assert kinds(check_turn_diff(changes, named_paths=frozenset())) == set()
    assert kinds(check_turn_diff(changes, named_paths=NAMED)) == {UNNAMED_FILE}


# --- what counts as a name: noise never names, real paths do ---------------------------------------

NOISE = ["cliente/servidor", "UI/UX", "e/ou", "Next.js", "e.g", "v1.2", "and/or", "I/O"]


@pytest.mark.parametrize("token", NOISE)
def test_a_word_that_only_looks_like_a_path_names_nothing_and_flags_nothing(token: str) -> None:
    message = f"Tidy the {token} section of the page"
    changed = ["src/widget.py", "docs/notes.md"]

    assert named_paths_of(message, changed=changed) == frozenset()
    check = check_turn_diff(
        [edit(p, None, "x\n") for p in changed],
        named_paths=named_paths_of(message, changed=changed),
    )
    assert UNNAMED_FILE not in kinds(check)


def test_a_path_the_turn_changed_is_named_and_so_is_one_the_workspace_has() -> None:
    changed = ["chimera/api/code_api.py"]

    assert named_paths_of("fix chimera/api/code_api.py", changed=changed) == {"chimera/api/code_api.py"}
    assert named_paths_of("look at README.md", changed=[], locate=lambda p: "file" if p == "README.md" else None) == {
        "README.md"
    }
    assert named_paths_of("look at api/code_api.py", changed=changed) == {"api/code_api.py"}


def test_a_bare_folder_names_its_children_with_or_without_a_slash() -> None:
    changed = ["chimera/api/code_api.py", "docs/notes.md"]

    assert named_paths_of("work in chimera/api", changed=changed) == {"chimera/api/"}
    assert named_paths_of("work in chimera/api/", changed=changed) == {"chimera/api/"}
    assert named_paths_of("work in chimera/api", changed=[], locate=lambda p: "dir" if p == "chimera/api" else None) == {
        "chimera/api/"
    }
    check = check_turn_diff(
        [edit(p, None, "x\n") for p in changed],
        named_paths=named_paths_of("work in chimera/api", changed=changed),
    )
    assert [(f.kind, f.path) for f in check.findings] == [(UNNAMED_FILE, "docs/notes.md")]


def test_absolute_and_escaping_paths_are_never_names() -> None:
    assert named_paths_of("see /etc/passwd", changed=[], locate=lambda p: "file") == frozenset()
    assert named_paths_of("see ../secrets.py", changed=[], locate=lambda p: "file") == frozenset()


# --- renames: the old path follows the new one -------------------------------------------------------

def test_a_rename_to_a_named_path_does_not_flag_its_old_path() -> None:
    body = "def widget():\n    return 4\n"
    changes = [edit("src/old_widget.py", body, None), edit("src/widget.py", None, body)]

    assert kinds(check_turn_diff(changes, named_paths=NAMED)) == set()


def test_a_rename_to_an_unnamed_path_is_still_flagged() -> None:
    body = "def widget():\n    return 4\n"
    changes = [edit("src/old_widget.py", body, None), edit("docs/moved.py", None, body)]

    assert {(f.kind, f.path) for f in check_turn_diff(changes, named_paths=NAMED).findings} == {
        (UNNAMED_FILE, "src/old_widget.py"),
        (UNNAMED_FILE, "docs/moved.py"),
    }


# --- scratch: only what the turn created -------------------------------------------------------------

def test_a_tracked_log_the_turn_edited_on_purpose_is_not_scratch() -> None:
    check = check_turn_diff([edit("run.log", "old\n", "new\n")], named_paths=None)

    assert kinds(check) == set()


def test_scratch_names_only_count_at_the_root_and_only_when_created() -> None:
    changes = [
        edit("assets/speech.wav", None, "RIFF"),
        edit("build/run.log", None, "x\n"),
        edit("run.log", None, "x\n"),
    ]

    check = check_turn_diff(changes, named_paths=None)

    assert [(f.kind, f.path) for f in check.findings] == [(SCRATCH_FILE, "run.log")]


def test_a_binary_scratch_file_the_turn_created_is_flagged() -> None:
    created = ChangedFile("speech.wav", existed_before=False, exists_after=True, before=None, after=None)

    assert kinds(check_turn_diff([created], named_paths=None)) == {SCRATCH_FILE}


# --- generated files and lockfiles -------------------------------------------------------------------

def test_a_generated_file_changed_with_anything_under_its_generator_is_a_regeneration() -> None:
    changes = [
        edit("chimera/api/code_api.py", "a = 1\n", "a = 2\n"),
        edit("apps/desktop/openapi.json", "{}\n", '{"openapi": "3.1"}\n'),
    ]
    assert kinds(check_turn_diff(changes, named_paths=None)) == set()

    tool = [edit("chimera/tools/x.py", "a\n", "b\n"), edit("apps/desktop/openapi.json", "{}\n", "{ }\n")]
    assert kinds(check_turn_diff(tool, named_paths=None)) == set()


def test_a_modified_binary_generated_file_still_counts_as_changed() -> None:
    binary = ChangedFile("apps/desktop/openapi.json", existed_before=True, exists_after=True, before=None, after=None)

    check = check_turn_diff([binary], named_paths=None)

    assert [(f.kind, f.path) for f in check.findings] == [(GENERATED_WITHOUT_SOURCE, "apps/desktop/openapi.json")]


def test_a_lockfile_moving_alone_is_not_a_finding_of_its_own() -> None:
    # `uv lock --upgrade-package` and `npm audit fix` move a lock without its manifest, legitimately.
    assert kinds(check_turn_diff([edit("uv.lock", "v1\n", "v2\n")], named_paths=None)) == set()


def test_a_lockfile_moved_with_its_manifest_is_part_of_that_request() -> None:
    changes = [edit("pyproject.toml", "[project]\n", "[project]\nx = 1\n"), edit("uv.lock", "v1\n", "v2\n")]

    assert kinds(check_turn_diff(changes, named_paths=frozenset({"pyproject.toml"}))) == set()


# --- measured or not: an unreadable turn is never clean ----------------------------------------------

def test_an_unmeasured_turn_with_no_visible_changes_is_not_clean() -> None:
    check = check_turn_diff([], named_paths=NAMED, incomplete_reason="the folder was too large to snapshot")

    assert check.clean is False and check.findings == ()
    assert check.to_payload()["measured"] is False
    assert "not measured" in str(check.note())
    assert "too large" in str(check.note())


def test_an_unmeasured_turn_keeps_its_findings_and_says_it_was_not_measured() -> None:
    check = check_turn_diff(
        [edit("run-output.txt", None, "x\n")], named_paths=NAMED, incomplete_reason="snapshot truncated",
    )

    payload = check.to_payload()
    assert payload["clean"] is False and payload["measured"] is False
    assert payload["measured_reason"] == "snapshot truncated"
    assert [f["kind"] for f in payload["findings"]] == [SCRATCH_FILE]


def test_a_truncated_record_reports_why_it_is_incomplete() -> None:
    truncated = TurnChange(before=FileSnapshot(), after={}, truncated=True)
    stated = TurnChange(before=FileSnapshot(), after={}, incomplete_reason="git status failed")
    whole = TurnChange(before=FileSnapshot(), after={})

    assert "too large" in incomplete_reason_of(truncated)
    assert incomplete_reason_of(stated) == "git status failed"
    assert incomplete_reason_of(whole) == ""


# --- the adapter from the workspace guard's own record ---------------------------------------------

def test_the_guard_record_becomes_changed_files_without_touching_disk() -> None:
    change = TurnChange(
        before=FileSnapshot(files={"src/widget.py": WIDGET_BEFORE, "old.txt": "gone\n"},
                            present={"src/widget.py", "old.txt"}),
        after={
            "src/widget.py": (True, "def widget():\n    return 5\n"),
            "docs/notes.md": (True, "hi\n"),
            "old.txt": (False, None),
        },
    )

    files = {c.path: c for c in changed_files_from(change)}

    assert files["src/widget.py"] == ChangedFile("src/widget.py", True, True, WIDGET_BEFORE, "def widget():\n    return 5\n")
    assert files["docs/notes.md"] == ChangedFile("docs/notes.md", False, True, None, "hi\n")
    assert files["old.txt"].exists_after is False and files["old.txt"].before == "gone\n"


# --- end to end through /api/code/turn ---------------------------------------------------------------

class _EditingAgent:
    """Writes the given files into the workspace, reporting each through `on_edit` as the real agent
    does, then answers once. A plain list of tools, no registry."""

    def __init__(
        self, ws: Path, writes: dict[str, str], answer: str, *, report_edits: bool = True,
        tool_names: list[str] | None = None,
    ) -> None:
        self.ws = ws
        self.writes = writes
        self.answer = answer
        self.tool_names = list(tool_names or [])
        self.report_edits = report_edits  # False: the writes are unreported, as a shell command's are
        self.tools: list[str] = []
        self.run_state = RunState()

    def run(self, task: str, *, on_edit: Any = None, history: list[Any] | None = None, **_: Any) -> AgentResult:
        for rel, text in self.writes.items():
            target = self.ws / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
            if on_edit is not None and self.report_edits:
                on_edit(rel, f"--- {rel}\n+++ {rel}\n")
        transcript = [*(history or []), {"role": "user", "content": task}, {"role": "assistant", "content": self.answer}]
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final", transcript=transcript,
                           tool_names=list(self.tool_names), model="test/model", usd=0.001)


def _code_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: _EditingAgent, message: str,
) -> tuple[Any, dict[str, Any], str]:
    pytest.importorskip("fastapi")
    pytest.importorskip("sse_starlette")
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.delenv("CHIMERA_VERIFIED_ANSWERS", raising=False)

    def build(*_args: Any, **_kwargs: Any) -> _EditingAgent:
        return agent

    monkeypatch.setattr(chimera.core, "Agent", build, raising=True)
    settings = Settings(_env_file=None, CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]

    class _Idle:
        def run(self, task: str, **_: Any) -> AgentResult:  # pragma: no cover — never used
            raise AssertionError("the turn must run on the agent the test built")

    client = TestClient(build_api_app(lambda: ChatSession(_Idle()), workspace=agent.ws, settings=settings))
    response = client.post("/api/code/turn", json={"message": message})
    assert response.status_code == 200, response.text
    frames: list[tuple[str, Any]] = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            frames.append((event, json.loads(line[len("data: "):])))
    done = next(data for name, data in frames if name == "done")
    session_id = next(data for name, data in frames if name == "session")["session_id"]
    return client, done, session_id


def _stored_last_answer(client: Any, session_id: str) -> str:
    """The last answer of a REOPENED conversation: what the stored file says, as the screen renders it."""
    stored = client.get(f"/api/code/sessions/{session_id}")
    assert stored.status_code == 200, stored.text
    exchanges = stored.json()["exchanges"]
    assert exchanges, "the conversation was stored with its exchanges"
    return str(exchanges[-1]["answer"])


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "widget.py").write_text(WIDGET_BEFORE, encoding="utf-8")
    return ws


def test_a_coding_turn_puts_its_self_check_on_done_and_stores_the_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _EditingAgent(
        _workspace(tmp_path),
        {
            "src/widget.py": "def widget():\n    return 5\n",  # named by the request: no finding
            "run-output.txt": "pytest exited 0\n",  # scratch at the root: a finding
            "docs/unrequested.md": "not asked for\n",  # outside the request: a finding
        },
        "Changed the widget to return 5.",
    )

    client, done, session_id = _code_turn(
        tmp_path, monkeypatch, agent, "Make widget() in src/widget.py return 5 (UI/UX, e.g. v1.2).",
    )

    check = done["self_check"]
    assert check["clean"] is False and check["measured"] is True
    assert {(f["kind"], f["path"]) for f in check["findings"]} == {
        (SCRATCH_FILE, "run-output.txt"),
        (UNNAMED_FILE, "docs/unrequested.md"),
    }
    assert done["answer"].startswith("Changed the widget to return 5.")
    assert "- scratch_file: run-output.txt" in done["answer"]
    # The note is in the STORED conversation, read back from disk, and it is what the next turn reads.
    stored = _stored_last_answer(client, session_id)
    assert "Self-check of this turn's own changes" in stored
    assert "- scratch_file: run-output.txt" in stored
    assert stored == done["answer"]


def test_a_coding_turn_with_a_clean_diff_says_nothing_anywhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _EditingAgent(_workspace(tmp_path), {"src/widget.py": "def widget():\n    return 5\n"}, "Done.")

    client, done, session_id = _code_turn(tmp_path, monkeypatch, agent, "Make widget() in src/widget.py return 5.")

    assert done["self_check"] == {"findings": [], "clean": True, "measured": True, "measured_reason": ""}
    assert done["answer"] == "Done."
    assert _stored_last_answer(client, session_id) == "Done."


def test_a_turn_that_edited_nothing_carries_no_self_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agent = _EditingAgent(_workspace(tmp_path), {}, "Just an answer.")

    _, done, _ = _code_turn(tmp_path, monkeypatch, agent, "What does widget() return?")

    assert "self_check" not in done
    assert done["answer"] == "Just an answer."


def test_findings_on_a_turn_with_no_answer_stay_on_done_and_are_not_stored_as_a_note_only_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _EditingAgent(_workspace(tmp_path), {"run-output.txt": "x\n"}, "")

    client, done, session_id = _code_turn(tmp_path, monkeypatch, agent, "Make widget() in src/widget.py return 5.")

    assert done["answer"] == ""
    assert SCRATCH_FILE in {f["kind"] for f in done["self_check"]["findings"]}
    assert "Self-check" not in _stored_last_answer(client, session_id)


# --- a shell write is checked too, and the lookup of names is bounded ----------------------------------

def test_a_scratch_file_a_shell_command_wrote_is_checked_with_no_edit_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _EditingAgent(_workspace(tmp_path), {"run-output.txt": "pytest exited 0\n"}, "Ran it.", report_edits=False)

    client, done, session_id = _code_turn(tmp_path, monkeypatch, agent, "Make widget() in src/widget.py return 5.")

    assert SCRATCH_FILE in {f["kind"] for f in done["self_check"]["findings"]}
    assert "- scratch_file: run-output.txt" in _stored_last_answer(client, session_id)


def test_the_name_lookup_reads_at_most_the_capped_number_of_tokens() -> None:
    count = MAX_NAME_TOKENS + 500
    changed = [f"src/f{i}.py" for i in range(count)]
    message = " ".join(f"f{i}.py" for i in range(count))

    named = named_paths_of(message, changed=changed)

    assert len(named) == MAX_NAME_TOKENS
    assert "f0.py" in named and f"f{MAX_NAME_TOKENS - 1}.py" in named
    assert f"f{MAX_NAME_TOKENS + 10}.py" not in named  # past the cap: never looked up


def test_a_long_request_against_a_large_change_set_stays_fast() -> None:
    changed = [f"pkg/module_{i}/part_{i}.py" for i in range(3000)]
    message = " ".join(f"token_{i}.txt" for i in range(20000))

    started = time.perf_counter()
    named_paths_of(message, changed=changed)

    assert time.perf_counter() - started < 10.0  # generous: the old scan took 17-27 s on this shape


# --- a folder too large to snapshot: say so only for a turn that could have changed it ---------------

def test_the_changing_tools_are_the_ones_that_write_or_run_a_command() -> None:
    assert could_have_changed(["read_file", "grep"], edited=False) is False
    assert could_have_changed([], edited=False) is False
    assert could_have_changed(["write_file"], edited=False) is True
    assert could_have_changed(["run_shell"], edited=False) is True
    assert could_have_changed(["read_file"], edited=True) is True


def _over_the_cap_workspace(tmp_path: Path) -> Path:
    ws = _workspace(tmp_path)
    bulk = ws / "bulk"
    bulk.mkdir()
    for index in range(5005):  # past the 5,000-file snapshot cap: the folder cannot be measured in full
        (bulk / f"f{index:05}.txt").write_text("x\n", encoding="utf-8")
    return ws


def test_a_plain_chat_turn_in_an_unmeasurable_folder_is_not_told_it_was_not_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _EditingAgent(_over_the_cap_workspace(tmp_path), {}, "Hello.", tool_names=["read_file"])

    client, done, session_id = _code_turn(tmp_path, monkeypatch, agent, "say hello")

    assert "self_check" not in done
    assert done["answer"] == "Hello."
    assert "not measured" not in _stored_last_answer(client, session_id)


def test_a_shell_write_in_an_unmeasurable_folder_is_reported_as_not_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _EditingAgent(
        _over_the_cap_workspace(tmp_path), {"run-output.txt": "x\n"}, "Ran it.",
        report_edits=False, tool_names=["run_shell"],
    )

    client, done, session_id = _code_turn(tmp_path, monkeypatch, agent, "run the tests")

    assert done["self_check"]["measured"] is False and done["self_check"]["clean"] is False
    assert "not measured" in done["answer"]
    assert "not measured" in _stored_last_answer(client, session_id)

