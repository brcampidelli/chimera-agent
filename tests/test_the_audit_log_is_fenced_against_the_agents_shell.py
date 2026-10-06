"""The audit log is fenced against the agent's own shell and code tools (study 31, G31-02).

The chain proves the file was not edited; it cannot say who edited it. A run that can reach
``<home>/audit.jsonl`` through ``run_shell`` can delete the lines that name it and re-chain the
rest, and the next ``verify()`` reports a clean chain — the tampering succeeded and the evidence
of it is gone. The approval queue has had this fence since #775 (`queue_fence.py`); the audit log
had none.

The fence is a narrowing, not a prevention — the plan says so, and these tests pin what it DOES
catch: the file by any spelling the file system resolves to, the route, the log's own code, the
CLI's reader, and the quoting a shell removes. What it does not catch (a path assembled at run
time) is pinned too, so the limit stays said rather than implied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.audit_fence import reaches_log
from chimera.tools.workspace import audit_refusal


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    """A data folder whose audit log exists — the fence reads the file system for identity."""
    (tmp_path / "audit.jsonl").write_text("", encoding="utf-8")
    return tmp_path


def test_a_command_that_deletes_the_log_is_refused(home: Path, tmp_path: Path) -> None:
    why = reaches_log("rm ~/.chimera/audit.jsonl", home=home, cwd=tmp_path)
    assert why == "it names the audit log"


def test_the_log_by_its_absolute_path(home: Path, tmp_path: Path) -> None:
    why = reaches_log(f"truncate -s 0 {home}/audit.jsonl", home=home, cwd=tmp_path)
    assert why == "it names the audit log"


def test_relative_to_the_workspace(home: Path, tmp_path: Path) -> None:
    # The workspace IS the home here: a relative `audit.jsonl` resolves onto the log.
    why = reaches_log("rm audit.jsonl", home=tmp_path, cwd=tmp_path)
    assert why == "it names the audit log"


def test_after_a_cd_the_command_makes(home: Path, tmp_path: Path) -> None:
    why = reaches_log(f"cd {home} && rm audit.jsonl", home=home, cwd=tmp_path)
    assert why == "it names the audit log"


def test_through_an_environment_variable(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    why = reaches_log("rm $CHIMERA_HOME/audit.jsonl", home=home, cwd=tmp_path)
    assert why == "it names the audit log"


def test_a_glob_that_matches_the_log(home: Path, tmp_path: Path) -> None:
    """The pattern does not spell the name, so the refusal comes from the path test — which
    resolves the glob and lands on the log by identity."""
    why = reaches_log(f"rm {home}/aud*.jsonl", home=home, cwd=tmp_path)
    assert why == "it names Chimera's audit log"


def test_quoting_a_shell_removes(home: Path, tmp_path: Path) -> None:
    """`aud""it.jsonl` is `audit.jsonl` to the shell that runs it — the bypass the queue fence
    measured on its own first version."""
    why = reaches_log('rm ~/.chimera/aud""it.jsonl', home=home, cwd=tmp_path)
    assert why == "it names the audit log"


def test_the_app_route(home: Path, tmp_path: Path) -> None:
    why = reaches_log(
        "curl -s http://localhost:8765/api/governance/audit > /tmp/a.jsonl", home=home, cwd=tmp_path
    )
    assert why == "it calls the app's audit route"


def test_the_logs_own_code_from_the_interpreter(home: Path, tmp_path: Path) -> None:
    """The import alone is enough — the program takes the path from argv, so the fence sees the
    CODE and not the file name: the point of fencing the code and not only the path."""
    why = reaches_log(
        "from chimera.governance.audit import AuditLog\nimport sys\nlog = AuditLog(sys.argv[1])",
        home=home,
        cwd=tmp_path,
    )
    assert why == "it calls the audit log's own code"


def test_the_cli_reader_as_the_subcommand(home: Path, tmp_path: Path) -> None:
    why = reaches_log("chimera audit --last 50", home=home, cwd=tmp_path)
    assert why == "it runs `chimera audit`"


def test_governance_audit_is_the_same_reader(home: Path, tmp_path: Path) -> None:
    why = reaches_log("chimera governance audit", home=home, cwd=tmp_path)
    assert why == "it runs `chimera audit`"


def test_a_mention_of_audit_in_prose_is_not_a_reach(home: Path, tmp_path: Path) -> None:
    """The fence reads paths and code, not topics: working ON the audit feature must not cost the
    agent every command that says the word."""
    assert reaches_log("grep -rn 'audit' chimera/governance/ | head", home=home, cwd=tmp_path) is None
    assert reaches_log("pytest -k audit -q", home=home, cwd=tmp_path) is None


def test_a_path_assembled_at_run_time_is_not_seen(home: Path, tmp_path: Path) -> None:
    """The limit, pinned rather than implied: the fence reads what a command SAYS, not what a shell
    computes. The structural answer is the deployment's (the log outside the agent's reach)."""
    assert reaches_log(
        "python -c \"import os; os.remove('audit' + '.jsonl')\"", home=home, cwd=tmp_path
    ) is None


def test_the_refusal_says_what_happened(home: Path, tmp_path: Path) -> None:
    out = audit_refusal("run_shell", "rm ~/.chimera/audit.jsonl", tmp_path)
    assert out is not None
    assert "did NOT run" in out
    assert "audit log" in out


def test_an_ordinary_command_pays_no_refusal(home: Path, tmp_path: Path) -> None:
    assert audit_refusal("run_shell", "make test", tmp_path) is None