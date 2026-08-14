"""The gateway must boot without the desktop extra installed.

This is a regression test for a broken published image. The Dockerfile installs `.[full]`, which
does not include `desktop`, so nothing FastAPI-shaped exists in the container that runs the 24/7
gateway. v0.45.0 shipped with the cron path importing `chimera.api.usage` — a JSONL reader with no
web anything in it — and because importing a leaf executes the package `__init__`, which eagerly
re-exported `build_api_app`, the whole web stack came with it. The container crash-looped on
`ModuleNotFoundError: No module named 'fastapi'`.

Nothing in the normal suite could catch it: the dev environment has FastAPI, so the import succeeds
and the bug is invisible. So these run in a **subprocess with `fastapi` made unimportable**, which is
the cheap way to reproduce a smaller install without building one.

The rule being defended: a module that does not serve HTTP must be importable without an HTTP
framework. Adding `desktop` to the image would have made the symptom go away and left the trap for
the next leaf import.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

#: Makes `import fastapi` (and the rest of the desktop stack) raise, the way a `.[full]` container
#: does — without needing one.
_BLOCK = """
import sys

class _Blocked:
    BLOCKED = ("fastapi", "starlette", "sse_starlette", "uvicorn")

    def find_module(self, name, path=None):
        return self if name.split(".")[0] in self.BLOCKED else None

    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in self.BLOCKED:
            raise ModuleNotFoundError(f"No module named {name.split('.')[0]!r}")
        return None

for _name in list(sys.modules):
    if _name.split(".")[0] in _Blocked.BLOCKED:
        del sys.modules[_name]
sys.meta_path.insert(0, _Blocked())
"""


def _run(body: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _BLOCK + textwrap.dedent(body)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_the_block_actually_blocks() -> None:
    """Without this the rest of the file proves nothing: if the guard silently let FastAPI through,
    every assertion below would pass in a dev environment that has it installed."""
    done = _run("import fastapi")

    assert done.returncode != 0
    assert "No module named 'fastapi'" in done.stderr


def test_the_usage_ledger_imports_without_the_web_stack() -> None:
    """The exact import the cron path makes, and the one that broke the published image."""
    done = _run(
        """
        from chimera.api.usage import UsageRecord, append_usage, spent_today
        print("ok", UsageRecord(model="m").model, callable(append_usage), callable(spent_today))
        """
    )

    assert done.returncode == 0, done.stderr
    assert "ok m True True" in done.stdout


def test_every_leaf_the_cli_reaches_for_imports_clean() -> None:
    """The other four. Each holds nothing FastAPI-shaped and each was dragging in the whole stack,
    so listing them here is what keeps the fix from decaying to the one module that hurt."""
    done = _run(
        """
        import chimera.api.roles
        import chimera.api.sessions
        import chimera.api.posture
        import chimera.api.config_api
        print("ok")
        """
    )

    assert done.returncode == 0, done.stderr
    assert "ok" in done.stdout


def test_the_cron_daemon_starts_without_fastapi() -> None:
    """End to end for the path that actually crash-looped: build the dispatch the cron daemon uses.

    A test that only imported modules would have passed while the daemon still died, because the
    failure was a transitive import inside a function body.
    """
    done = _run(
        """
        from chimera.scheduler import make_agent_dispatch
        from chimera.api.usage import UsageRecord, append_usage, spent_today  # the cron's import
        dispatch = make_agent_dispatch(lambda task: "ok")
        print("ok", callable(dispatch))
        """
    )

    assert done.returncode == 0, done.stderr
    assert "ok True" in done.stdout


def test_the_desktop_api_still_needs_its_extra_and_says_so() -> None:
    """The counterpart. `chimera app` genuinely needs FastAPI, and asking for it without the extra
    must fail loudly — a lazy package that swallowed the error would turn a missing dependency into
    a mysterious AttributeError at the first request."""
    done = _run(
        """
        import chimera.api
        try:
            chimera.api.build_api_app
        except ModuleNotFoundError as exc:
            print("raised", "fastapi" in str(exc))
        """
    )

    assert done.returncode == 0, done.stderr
    assert "raised True" in done.stdout
