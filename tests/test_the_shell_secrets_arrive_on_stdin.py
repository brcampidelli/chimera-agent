"""The desktop shell's secrets reach the backend on stdin, not in its starting environment.

``os.environ.pop`` removes a variable from this process's copy. ``/proc/<pid>/environ`` (Linux),
``ps eww`` (macOS) and Process Explorer (Windows) go on showing the environment the process was
STARTED with to every process of the same user, the agent's ``run_shell`` included, and the screen
credential is what separates the owner's click from everything else the agent can reach. So the shell
writes one JSON line to the child's stdin and closes it (``apps/desktop/src-tauri/src/launch_secrets.rs``),
and this side reads it first thing (``chimera/launch_secrets.py``).

What these pin: the credential arrives from stdin and reaches ``ScreenCredential``; nothing secret
(the bearer token included, which would be readable on Windows through ``putenv``) is put in the
environment; a real child cannot find the value in its starting environment; the env variable still
works where there is no channel and is IGNORED where the shell said there is one; and every way the
line can fail to arrive ends the process, because a backend that came up without its token would
serve the API unguarded.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from chimera import launch_secrets as ls
from chimera.api import screen_credential as sc
from chimera.config import get_settings

SCREEN = "s" * 64
LAUNCHER = "l" * 64
SERVER = "b" * 64
REPO = Path(__file__).resolve().parent.parent


def _line(**values: str) -> bytes:
    return (json.dumps(values) + "\n").encode()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        sc.ENV_VAR, sc.LAUNCHER_ENV, ls.MARKER_ENV, "CHIMERA_SERVER_TOKEN", "CHIMERA_TAURI_SIDECAR",
    ):
        monkeypatch.delenv(name, raising=False)
    ls._forget_for_tests()
    get_settings.cache_clear()
    yield
    ls._forget_for_tests()
    get_settings.cache_clear()
    for name in (sc.ENV_VAR, sc.LAUNCHER_ENV, ls.MARKER_ENV, "CHIMERA_SERVER_TOKEN"):
        os.environ.pop(name, None)


def _under_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(ls.MARKER_ENV, "stdin")


def test_the_credential_read_from_stdin_is_the_one_the_backend_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    _under_the_shell(monkeypatch)
    stream = io.BytesIO(_line(server_token=SERVER, screen_token=SCREEN, launcher_key=LAUNCHER))
    assert ls.adopt_from_stdin(stream) is True

    credential = sc.for_mode(None, server_token=SERVER)

    assert credential.value == SCREEN
    assert credential.in_page is False, "the shell's backend must not offer it in a page"
    assert credential.launcher_key == LAUNCHER


def test_no_secret_is_put_in_the_environment_and_settings_still_gets_the_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _under_the_shell(monkeypatch)
    ls.adopt_from_stdin(io.BytesIO(_line(server_token=SERVER, screen_token=SCREEN, launcher_key=LAUNCHER)))
    sc.for_mode(None, server_token=SERVER)

    values = " ".join(os.environ.values())
    assert SCREEN not in values and LAUNCHER not in values and SERVER not in values
    assert "CHIMERA_SERVER_TOKEN" not in os.environ, "on Windows putenv rewrites the live environment block"
    assert ls.MARKER_ENV not in os.environ
    assert get_settings().server_token == SERVER, "the guard would see no token"


def test_a_token_set_after_start_up_outranks_the_one_from_the_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner saving a new token (``PATCH /api/config``) must not be undone by the launch value."""
    _under_the_shell(monkeypatch)
    ls.adopt_from_stdin(io.BytesIO(_line(server_token=SERVER)))
    assert get_settings().server_token == SERVER
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "n" * 64)
    get_settings.cache_clear()
    assert get_settings().server_token == "n" * 64


def test_a_value_is_handed_over_once(monkeypatch: pytest.MonkeyPatch) -> None:
    _under_the_shell(monkeypatch)
    ls.adopt_from_stdin(io.BytesIO(_line(screen_token=SCREEN)))
    assert sc.for_mode(None, server_token=SERVER).value == SCREEN
    assert sc.for_mode(None, server_token=SERVER).value is None, "the held copy outlived its read"


def test_a_screen_credential_equal_to_the_bearer_token_is_still_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _under_the_shell(monkeypatch)
    ls.adopt_from_stdin(io.BytesIO(_line(server_token=SERVER, screen_token=SERVER)))
    assert sc.for_mode(None, server_token=SERVER).value is None


def test_when_the_shell_uses_the_channel_an_environment_value_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    _under_the_shell(monkeypatch)
    monkeypatch.setenv(sc.ENV_VAR, "e" * 64)
    ls.adopt_from_stdin(io.BytesIO(_line(screen_token=SCREEN)))

    credential = sc.for_mode(None, server_token=SERVER)

    assert credential.value == SCREEN
    assert sc.ENV_VAR not in os.environ, "the variable was left behind for a child to inherit"


def test_a_service_and_a_browser_never_consume_the_channels_screen_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mode is checked before the value is read: only the desktop shell's backend adopts one."""
    _under_the_shell(monkeypatch)
    ls.adopt_from_stdin(io.BytesIO(_line(screen_token=SCREEN, launcher_key=LAUNCHER)))
    service = sc.for_mode("service", server_token=SERVER)
    assert service.value is None and service.launcher_key == LAUNCHER

    monkeypatch.delenv("CHIMERA_TAURI_SIDECAR")
    monkeypatch.setenv(sc.ENV_VAR, "e" * 64)
    browser = sc.for_mode(None, server_token=SERVER)
    assert browser.in_page is True and browser.value not in (SCREEN, "e" * 64)
    assert sc.ENV_VAR not in os.environ


# --- the line did not arrive: the backend must not come up without its token -------------------------


@pytest.mark.parametrize(
    "raw",
    [b"", b"not json\n", b"[1, 2]\n", b'"just a string"\n', b"\xff\xfe\n", b'{"server_token": "' + b"x" * 20_000],
    ids=["eof", "bad-json", "array", "string", "not-utf8", "truncated"],
)
def test_a_line_that_is_not_a_json_object_ends_the_process(monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    _under_the_shell(monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        ls.adopt_from_stdin(io.BytesIO(raw))
    assert stopped.value.code == ls.EXIT_CODE
    assert ls.server_token() is None and "CHIMERA_SERVER_TOKEN" not in os.environ


def test_no_usable_stdin_ends_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    _under_the_shell(monkeypatch)
    monkeypatch.setattr(ls, "_stdin_stream", lambda: None)
    with pytest.raises(SystemExit) as stopped:
        ls.adopt_from_stdin()
    assert stopped.value.code == ls.EXIT_CODE


def test_a_shell_that_writes_nothing_ends_the_process_instead_of_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    _under_the_shell(monkeypatch)

    def exits(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(ls, "_hard_exit", exits)
    read_end, write_end = os.pipe()
    silent = os.fdopen(read_end, "rb")
    try:
        with pytest.raises(SystemExit) as stopped:
            ls.adopt_from_stdin(silent, timeout=0.2)
        assert stopped.value.code == ls.EXIT_CODE
    finally:
        # The write end first: the abandoned reader thread holds the stream's lock until it sees EOF.
        os.close(write_end)
        silent.close()


def test_a_valid_object_without_a_key_is_legitimate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service the shell starts with no token of its own gets only the launcher key."""
    _under_the_shell(monkeypatch)
    assert ls.adopt_from_stdin(io.BytesIO(_line(launcher_key=LAUNCHER))) is True
    assert ls.server_token() is None
    assert sc.for_mode("service").launcher_key == LAUNCHER
    ls._forget_for_tests()
    monkeypatch.setenv(ls.MARKER_ENV, "stdin")
    assert ls.adopt_from_stdin(io.BytesIO(b"{}\n")) is True


def test_the_channel_is_honoured_only_under_the_desktop_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ls.MARKER_ENV, "stdin")  # no CHIMERA_TAURI_SIDECAR
    stream = io.BytesIO(_line(server_token=SERVER, screen_token=SCREEN))
    assert ls.adopt_from_stdin(stream) is False
    assert stream.tell() == 0, "something that is not the shell had its stdin read"
    assert ls.server_token() is None


def test_without_the_channel_the_environment_fallback_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.ENV_VAR, SCREEN)
    monkeypatch.setenv(sc.LAUNCHER_ENV, LAUNCHER)
    assert ls.adopt_from_stdin(io.BytesIO(b"")) is False

    credential = sc.for_mode(None, server_token=SERVER)

    assert credential.value == SCREEN and credential.launcher_key == LAUNCHER
    assert sc.ENV_VAR not in os.environ and sc.LAUNCHER_ENV not in os.environ


def test_a_launcher_key_from_the_channel_must_still_look_like_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _under_the_shell(monkeypatch)
    ls.adopt_from_stdin(io.BytesIO(_line(launcher_key="short")))
    assert sc.take_launcher_key() is None


# --- real processes, real pipes ------------------------------------------------------------------------

_CHILD = textwrap.dedent(
    """
    import json, os, sys
    from chimera import launch_secrets
    from chimera.api import screen_credential as sc
    from chimera.config import get_settings

    launch_secrets.adopt_from_stdin(timeout=float(os.environ.get("TEST_TIMEOUT", "10")))
    credential = sc.for_mode(None, server_token=get_settings().server_token)
    startup_block = ""
    if os.path.exists("/proc/self/environ"):
        with open("/proc/self/environ", "rb") as f:
            startup_block = f.read().decode("utf-8", "replace")
    print(json.dumps({
        "value": credential.value,
        "token": get_settings().server_token,
        "in_environ": "CHIMERA_SERVER_TOKEN" in os.environ,
        "startup_block": startup_block,
        "stdin_after": sys.stdin.read(),
    }))
    """
)


def _child_env(**extra: str) -> dict[str, str]:
    env = {
        k: v for k, v in os.environ.items()
        if k not in (sc.ENV_VAR, sc.LAUNCHER_ENV, "CHIMERA_SERVER_TOKEN")
    }
    env.update({"CHIMERA_TAURI_SIDECAR": "1", ls.MARKER_ENV: "stdin", "PYTHONPATH": str(REPO), **extra})
    return env


def test_a_real_child_gets_the_credential_and_its_starting_environment_never_held_it() -> None:
    """End to end over a real pipe: the property the whole change exists for.

    The environment handed to the child is checked for the secrets BEFORE it starts: that is the
    block ``/proc/<pid>/environ`` and ``ps eww`` would show. Where ``/proc`` exists the child also
    reads its own copy back, which is exactly what the agent's shell could do.
    """
    env = _child_env()
    assert not any(SCREEN in v or SERVER in v or LAUNCHER in v for v in env.values())

    done = subprocess.run(  # noqa: S603 - the interpreter running this suite, a fixed script
        [sys.executable, "-c", _CHILD],
        input=_line(server_token=SERVER, screen_token=SCREEN, launcher_key=LAUNCHER),
        capture_output=True, env=env, cwd=REPO, timeout=120, check=True,
    )

    answer = json.loads(done.stdout.decode().strip().splitlines()[-1])
    assert answer["value"] == SCREEN and answer["token"] == SERVER
    assert answer["in_environ"] is False, "the bearer token was put in the live environment block"
    assert SCREEN not in answer["startup_block"] and SERVER not in answer["startup_block"]
    assert answer["stdin_after"] == "", "stdin still held the pipe after the read"


@pytest.mark.parametrize("raw", [b"", b"garbage\n", b"[]\n"], ids=["eof", "bad-json", "array"])
def test_a_real_backend_whose_line_is_bad_exits_and_never_comes_up(raw: bytes) -> None:
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", _CHILD], input=raw, capture_output=True, env=_child_env(),
        cwd=REPO, timeout=120,
    )
    assert done.returncode == ls.EXIT_CODE
    assert b'"token"' not in done.stdout, "the backend went on to run with no token"


def test_a_real_backend_whose_shell_never_writes_exits_instead_of_hanging() -> None:
    """The deadlock case: real fd 0, a pipe that stays open with nothing on it, a short timeout."""
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", _CHILD], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=_child_env(TEST_TIMEOUT="1"), cwd=REPO,
    )
    try:
        child.wait(timeout=120)  # not communicate(): that closes stdin, and EOF is a different case
        assert child.stdout is not None
        out = child.stdout.read()
    finally:
        child.kill()
        for pipe in (child.stdin, child.stdout, child.stderr):
            if pipe is not None:
                pipe.close()
    assert child.returncode == ls.EXIT_CODE
    assert b'"token"' not in out
