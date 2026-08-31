"""The surface that runs most often was the only one with no harness at all.

`chimera solve`, the Code screen and `POST /api/runs` all wrap the worker in the autonomous loop:
snapshot, verify, revert, retry, receipt. The cron dispatch called `agent.run(job.action)` directly.
So the path that fires unattended every day, for months, had no gate, no rollback, no retry and no
entry in `runs.jsonl` — nothing was watching the thing that runs when nobody is watching.

**The gate is opt-in, and that is the design rather than a caution.** `unverified_and_unchanged`
fails an attempt that changed no file, and most scheduled jobs are reports: summarise the day, check
a price, post to a channel. Arming it for everyone would fail every one of them with "this task
requires editing code" — which is a defect this project already measured on its own release. With no
guard the diff gate cannot fire at all (`diff_productive` stays None), so a report job behaves
exactly as it did and gains only the accounting.

⚠️ **Coverage stated rather than implied.** The cron dispatch is a closure inside
`_start_cron_daemon`, a CLI entrypoint that spawns a thread; reaching it behaviourally would mean
standing up a daemon and a provider. So the wiring below is asserted **at source level**, which is
weaker than execution and is named as such. The `require_diff` half is asserted behaviourally,
through the builder the route really uses.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.providers import CompletionResult


def _dispatch_source() -> str:
    """The scheduled dispatch's source.

    It moved out of the CLI command into `chimera/scheduler/job_runner.py`, precisely so that these
    properties could be checked by RUNNING it — see
    `tests/test_the_scheduled_dispatch_can_be_driven.py`, which now covers the receipt, the gate and
    the verdict by driving a real job. What is asserted here is what remains structural: which
    classes the path is built from.
    """
    from chimera.scheduler import job_runner

    return inspect.getsource(job_runner.make_run_job)


# --------------------------------------------------------------------------- the job declares it


def test_a_job_can_declare_what_proves_its_work() -> None:
    from chimera.scheduler.models import CronJob

    job = CronJob(id="j", name="n", schedule="0 7 * * *", action="do the thing")

    assert job.verify == "", "the gate must be off unless a job asks for it"
    assert job.max_attempts == 1, "one shot stays the default; a retry without a gate proves nothing"


# --------------------------------------------------------------------------- the wiring (source)


def test_the_dispatch_runs_the_harness_and_not_the_bare_agent() -> None:
    source = _dispatch_source()

    assert "AutonomousAgent(" in source
    assert "result = agent.run(job.action)" not in source, "the bare worker is back on the cron path"


def test_the_gate_is_armed_only_by_a_job_that_asked_for_one() -> None:
    """Both halves on the same condition. A verifier with no guard cannot revert, and a guard with
    no verifier arms the diff gate on report jobs — which is the failure this avoids."""
    source = _dispatch_source()

    assert "CommandVerifier(job.verify, job_root) if gated else None" in source
    assert "WorkspaceGuard(job_root) if gated else None" in source
    assert 'gated = bool(job.verify.strip())' in source


def test_the_dispatch_writes_a_receipt() -> None:
    """`runs.jsonl` is what the Runs screen reads, and a job that has fired nightly for a month
    left nothing there to read."""
    assert 'run_log=settings.home / "runs.jsonl"' in _dispatch_source()


def test_cron_still_has_no_reviewer() -> None:
    """The control. Wrapping the worker in the loop must not quietly add a model call per dispatch
    to grade prose nobody asked to have graded."""
    source = _dispatch_source()

    assert "use_manager=False" in source
    assert "manager=None" in source


def test_the_usage_row_sums_every_attempt() -> None:
    """With `max_attempts > 1` a dispatch can pay for several, and reporting the last one would
    understate the cost of exactly the configuration that costs most."""
    source = _dispatch_source()

    assert "sum(int(a.prompt_tokens or 0) for a in attempts)" in source
    # And the all-or-nothing dollar rule, so one unpriced attempt makes the total unknown, never
    # smaller — the same rule the crew receipts and the spend ceiling follow.
    assert "None if any(v is None for v in usd_values)" in source


# --------------------------------------------------------------------------- require_diff


class _Backend:
    def complete(self, messages: list[dict[str, str]], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content="ok", model="fake", prompt_tokens=1, completion_tokens=1)


@pytest.fixture(autouse=True)
def _no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.providers.LLMGateway", _Backend)


def _agent(tmp_path: Path, **fields: Any) -> Any:
    from chimera.api.app import RunRequest, _build_solve_agent

    req = RunRequest(task="x", workspace=str(tmp_path), **fields)
    return _build_solve_agent(
        req, tmp_path, lambda _e: None, Settings(CHIMERA_HOME=str(tmp_path / "h"))
    )


def test_require_diff_reaches_the_agent(tmp_path: Path) -> None:
    """It existed only as a CLI flag, so no screen could arm it — and this project's own SWE-bench
    decomposition attributes HALF the measured scaffold lift to it (+4.9pp of +9.8pp), because a
    passing test proves nothing about a patch that is empty."""
    assert _agent(tmp_path, require_diff=True).require_diff is True


def test_require_diff_stays_off_unless_asked(tmp_path: Path) -> None:
    """The control, and not a formality: it is right for a code task and wrong for a question. A run
    asked to explain an architecture changes no files and is not thereby a failure."""
    assert _agent(tmp_path).require_diff is False
