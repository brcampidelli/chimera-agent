"""The scheduled dispatch, driven end to end — the test that did not exist when it mattered.

Two defects reached users through this one function, and both for the same reason: it was a closure
inside a CLI command, so nothing could call it. The tests that stood in for it handed a
``JobOutcome`` straight to the part that RECORDS one — every step of the path except the step that
was broken, twice over:

* the verdict it returns (a bare string was read as ``ok``, so a job whose gate rejected every
  attempt kept a green row and a zero failure count);
* the workspace it names (left unset, so every scheduled receipt was filed under no project and was
  excluded from every filtered list by design).

Now that ``make_run_job`` is importable, both are checked by running it. No network: the backend is
a fake and the verify command is a local ``python -c``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.scheduler.job_runner import make_run_job
from chimera.scheduler.models import CronJob, JobOutcome


class _Result:
    """The shape the agent loop reads off a completion."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "fake/model"
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls: list[Any] = []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Backend:
    """Answers in prose and calls nothing, so the run ends on its own and the GATE decides."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        return _Result("pronto")


def _run(tmp_path: Path, *, verify: str, attempts: int = 1) -> tuple[JobOutcome, Path, _Backend]:
    home = tmp_path / "home"
    projeto = tmp_path / "projeto"
    projeto.mkdir(parents=True, exist_ok=True)
    settings = Settings(**{"CHIMERA_HOME": str(home)})
    backend = _Backend()
    run_job = make_run_job(
        settings=settings,
        backend=backend,
        workspace=tmp_path / "nao-e-a-pasta-do-job",
        model="fake/model",
        max_steps=3,
        usage_path=home / "usage.jsonl",
    )
    job = CronJob(
        id="j1", name="nightly", trigger="cron", schedule="* * * * *",
        action="escreva algo", workspace=str(projeto), verify=verify, max_attempts=attempts,
    )
    return run_job(job), home, backend


def _recibo(home: Path) -> dict[str, Any]:
    bruto = (home / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    linhas = [linha for linha in bruto if linha.strip()]
    assert linhas, "the dispatch left no receipt at all"
    return dict(json.loads(linhas[-1]))


# --- the verdict --------------------------------------------------------------------------------


def test_a_gate_that_rejects_makes_the_dispatch_say_so(tmp_path: Path) -> None:
    """The defect, reproduced through the real producer: a bare answer string is read as ``ok`` by
    the dispatch, and that is how a job that kept nothing showed a green row."""
    outcome, _, backend = _run(tmp_path, verify=f'"{sys.executable}" -c "import sys; sys.exit(1)"')

    assert backend.calls > 0, "the agent never ran, so nothing here is about a gate"
    assert isinstance(outcome, JobOutcome)
    assert outcome.ok is False, "the schedule was told the job was fine"


def test_a_gate_that_passes_leaves_the_verdict_alone(tmp_path: Path) -> None:
    outcome, _, _ = _run(tmp_path, verify=f'"{sys.executable}" -c "pass"')

    assert outcome.ok is True


def test_a_job_with_no_gate_reports_no_verdict(tmp_path: Path) -> None:
    """Without a gate nothing could reject the work, and ``success`` there reflects gates this path
    does not run — reporting it would turn every ungated job into a failure."""
    outcome, _, _ = _run(tmp_path, verify="")

    assert outcome.ok is True


class _ExpensiveBackend(_Backend):
    """Calls a tool first, so the loop takes a SECOND step — which is where the ceiling can bite.

    A backend that answers on step one never gives the budget a chance to refuse anything: the cap
    is checked BEFORE each call and only the first one had happened. Measured while writing this:
    with a one-step backend an ungated run always ends successfully, so the assertion below could
    not tell the branch it guards from its absence.
    """

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        r = _Result("pronto")
        r.model = "openrouter/deepseek/deepseek-chat-v3.1"  # priced, so the spend accumulates
        r.prompt_tokens, r.completion_tokens = 400_000, 400_000
        if self.calls == 1:
            from chimera.providers.gateway import ToolCall

            r.tool_calls = [ToolCall(id="c1", name="list_dir", arguments={"path": "."})]
        return r


def test_an_ungated_job_that_failed_is_still_not_a_rejection(tmp_path: Path) -> None:
    """The case that separates "no gate" from "the gate said no", and the reason the branch exists.

    A job with no verify can still end unsuccessfully — this one stops on its own dollar ceiling.
    Nothing could have REJECTED the work, so the schedule must not be told there was, or every
    ungated job that runs out of money is filed as work a gate threw away.
    """
    home = tmp_path / "home"
    backend = _ExpensiveBackend()
    run_job = make_run_job(
        settings=Settings(**{"CHIMERA_HOME": str(home)}), backend=backend, workspace=tmp_path,
        model="openrouter/deepseek/deepseek-chat-v3.1", max_steps=6,
        usage_path=home / "usage.jsonl",
    )
    job = CronJob(
        id="j", name="n", trigger="cron", schedule="* * * * *", action="x",
        workspace=str(tmp_path), verify="", max_usd=0.000001,
    )

    outcome = run_job(job)

    assert backend.calls >= 1
    assert "spend cap" in outcome.answer.lower(), (
        "the run did not reach its ceiling, so this is not testing the ungated-failure case"
    )
    assert outcome.ok is True, "an ungated job was reported as though a gate had rejected it"


def test_the_answer_survives_the_verdict(tmp_path: Path) -> None:
    """The work was thrown away; the report of it was not. Someone reading a webhook needs to learn
    the job produced nothing, which is exactly the message that matters most."""
    outcome, _, _ = _run(tmp_path, verify=f'"{sys.executable}" -c "import sys; sys.exit(1)"')

    assert outcome.answer, "the dispatch returned a verdict with nothing to deliver"


# --- the workspace ------------------------------------------------------------------------------


def test_the_receipt_is_filed_under_the_job_folder(tmp_path: Path) -> None:
    """The other defect: the loop was given its run log and not its workspace, so every scheduled
    receipt was unattributable — and an unattributable receipt is left out of every filtered list
    by design, which made each one unfindable from the only screen that would look."""
    _, home, _ = _run(tmp_path, verify=f'"{sys.executable}" -c "pass"')

    recibo = _recibo(home)

    assert recibo["workspace"], "the receipt names no project"
    assert Path(recibo["workspace"]).name == "projeto"


def test_the_folder_is_the_job_s_not_the_process_s(tmp_path: Path) -> None:
    """`workspace` (the process root) and `job.workspace` are different things, and attributing
    every scheduled run to wherever the daemon started is worse than the empty string it replaced."""
    _, home, _ = _run(tmp_path, verify="")

    assert Path(_recibo(home)["workspace"]).name != "nao-e-a-pasta-do-job"


# --- the money ----------------------------------------------------------------------------------


def test_the_dispatch_writes_what_it_spent(tmp_path: Path) -> None:
    """The daily cap is read from this log, so a dispatch that does not write to it leaves the cap
    blind to exactly the spend it exists to bound."""
    _, home, _ = _run(tmp_path, verify="")

    assert (home / "usage.jsonl").exists(), "the usage log was never written"
    linha = json.loads((home / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert linha["session_id"].startswith("cron:")


def test_a_daily_cap_already_reached_refuses_before_spending(tmp_path: Path) -> None:
    """Refusal, not a cheaper run: the point of a daily ceiling is that the job does not start."""
    from chimera.orchestration.budget import BudgetExceeded

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "usage.jsonl").write_text(
        json.dumps({"ts": "2000-01-01T00:00:00+00:00", "session_id": "x", "usd": 999.0}) + "\n",
        encoding="utf-8",
    )
    settings = Settings(**{"CHIMERA_HOME": str(home), "CHIMERA_DAILY_USD_CAP": "0.01"})
    backend = _Backend()
    run_job = make_run_job(
        settings=settings, backend=backend, workspace=tmp_path, model="fake/model",
        max_steps=3, usage_path=home / "usage.jsonl",
    )
    job = CronJob(id="j", name="n", trigger="cron", schedule="* * * * *", action="x")

    # The cap reads TODAY, and the row above is from the year 2000 — so this must NOT refuse, which
    # is the half that proves the refusal below is about the cap and not about any row existing.
    run_job(job)
    assert backend.calls > 0

    (home / "usage.jsonl").write_text(
        json.dumps({
            "ts": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
            "session_id": "x", "usd": 999.0,
        }) + "\n",
        encoding="utf-8",
    )
    antes = backend.calls
    with pytest.raises(BudgetExceeded):
        run_job(job)
    assert backend.calls == antes, "the job spent before being refused"


class _TwoAttemptBackend(_Backend):
    """Answers each time, so a two-attempt job really makes two rounds of calls."""

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        r = _Result("pronto")
        r.model = "openrouter/deepseek/deepseek-chat-v3.1"
        r.prompt_tokens, r.completion_tokens = 1_000, 100
        return r


def test_the_usage_row_sums_every_attempt(tmp_path: Path) -> None:
    """One row per dispatch, holding the whole dispatch.

    With `max_attempts > 1` a job can pay several times, and reporting only the last would
    understate exactly the configuration that costs most — the daily cap is read from this log, so
    an understated row is a ceiling that lets the next job through when it should not.

    Asserted by RUNNING a two-attempt job rather than by reading the summation out of the source,
    which is what this was checked by until the dispatch became reachable.
    """
    home = tmp_path / "home"
    projeto = tmp_path / "projeto"
    projeto.mkdir(parents=True, exist_ok=True)
    backend = _TwoAttemptBackend()
    run_job = make_run_job(
        settings=Settings(**{"CHIMERA_HOME": str(home)}), backend=backend, workspace=tmp_path,
        model="openrouter/deepseek/deepseek-chat-v3.1", max_steps=3,
        usage_path=home / "usage.jsonl",
    )
    job = CronJob(
        id="j", name="n", trigger="cron", schedule="* * * * *", action="x",
        workspace=str(projeto), verify=f'"{sys.executable}" -c "import sys; sys.exit(1)"',
        max_attempts=2,
    )

    run_job(job)

    linha = json.loads((home / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    recibo = _recibo(home)
    tentativas = recibo["attempts"]
    assert len(tentativas) == 2, "the job did not make two attempts, so nothing here is a sum"
    assert linha["prompt_tokens"] == sum(a["prompt_tokens"] for a in tentativas)
    assert round(linha["usd"], 6) == round(sum(a["usd"] for a in tentativas), 6)


def test_cron_still_has_no_reviewer(tmp_path: Path) -> None:
    """A reviewer would be a model call per attempt spent grading prose nobody asked to be graded.

    Counted rather than read off the source: a two-attempt run makes exactly the calls its worker
    makes, and a reviewer would show up as extra ones.
    """
    home = tmp_path / "home"
    backend = _TwoAttemptBackend()
    run_job = make_run_job(
        settings=Settings(**{"CHIMERA_HOME": str(home)}), backend=backend, workspace=tmp_path,
        model="openrouter/deepseek/deepseek-chat-v3.1", max_steps=3,
        usage_path=home / "usage.jsonl",
    )
    job = CronJob(
        id="j", name="n", trigger="cron", schedule="* * * * *", action="x",
        workspace=str(tmp_path), verify=f'"{sys.executable}" -c "import sys; sys.exit(1)"',
        max_attempts=2,
    )

    run_job(job)

    assert backend.calls == 2, (
        f"{backend.calls} model calls for two attempts — something is grading the work"
    )
