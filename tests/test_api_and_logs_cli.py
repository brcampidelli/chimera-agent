"""The API CLI confirms mutations and never leaks the local bearer token; logs stay scoped."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from chimera.cli.commands import apilogs
from chimera.cli.main import app
from chimera.config import get_settings

runner = CliRunner()

TOKEN = "secret-test-token"


class _FakeClient:
    """Stands in for `httpx.Client`, the one door the command uses, and records what went in.

    Faking `httpx.request` instead proved nothing once the command moved to a client: the real
    client then tried 127.0.0.1:8765, and whatever happened to listen there answered the test.
    """

    def __init__(self, record: list[dict[str, Any]], respond: Callable[[], httpx.Response]) -> None:
        self.record = record
        self.respond = respond

    def __call__(self, **kwargs: Any) -> _FakeClient:
        self.record.append({"client": kwargs})
        return self

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.record.append({"method": method, "url": url, **kwargs})
        return self.respond()


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[list[dict[str, Any]]]:
    """A home with no announcement, no token, and a fake client answering 200."""
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home-without-announcement"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.delenv("CHIMERA_SERVER_TOKEN")
    get_settings.cache_clear()
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(
        apilogs.httpx, "Client", _FakeClient(record, lambda: httpx.Response(200, text="ok"))
    )
    yield record
    get_settings.cache_clear()


def _requests(record: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in record if "method" in entry]


def _clients(record: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry["client"] for entry in record if "client" in entry]


def _use_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", TOKEN)
    get_settings.cache_clear()


def test_api_refuses_mutating_method_without_confirmation(calls: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["api", "POST", "/api/something"])
    assert result.exit_code == 2
    assert "--yes" in result.stdout
    assert calls == [], "nothing may be sent before the explicit confirmation"


def test_api_sends_a_confirmed_mutation_with_its_json_body(calls: list[dict[str, Any]]) -> None:
    result = runner.invoke(
        app, ["api", "POST", "/api/thing", "--yes", "-d", '{"name": "x", "n": 2}']
    )
    assert result.exit_code == 0, result.stdout
    sent = _requests(calls)
    assert [(c["method"], c["url"]) for c in sent] == [
        ("POST", "http://127.0.0.1:8765/api/thing")
    ]
    # Parsed, not forwarded as a string: the server receives an object, not a quoted blob.
    assert sent[0]["json"] == {"name": "x", "n": 2}


def test_api_get_needs_no_confirmation(calls: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["api", "get", "/api/health"])
    assert result.exit_code == 0, result.stdout
    assert [c["method"] for c in _requests(calls)] == ["GET"]
    assert "HTTP 200" in result.stdout


def test_api_rejects_invalid_json_before_sending(calls: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["api", "POST", "/api/x", "--yes", "-d", "{not json"])
    assert result.exit_code == 2
    assert "Invalid JSON body" in result.stdout
    assert calls == []


def test_api_rejects_a_malformed_header_before_sending(calls: list[dict[str, Any]]) -> None:
    result = runner.invoke(app, ["api", "GET", "/api/x", "-H", "no-colon-here"])
    assert result.exit_code == 2
    assert "Invalid header" in result.stdout
    assert calls == []


def test_api_sends_token_without_printing_it(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]
) -> None:
    _use_token(monkeypatch)
    monkeypatch.setattr(
        apilogs.httpx,
        "Client",
        _FakeClient(calls, lambda: httpx.Response(200, text=f"{TOKEN} was echoed")),
    )
    result = runner.invoke(app, ["api", "GET", "/api/health", "-H", "Authorization: Bearer x"])
    assert result.exit_code == 0, result.stdout
    assert "HTTP 200" in result.stdout
    assert TOKEN not in result.stdout
    assert "[redacted]" in result.stdout
    sent = _requests(calls)
    # The configured token wins over an ad-hoc header.
    assert sent[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert sent[0]["url"] == "http://127.0.0.1:8765/api/health"


def test_api_discovers_the_desktop_bound_port_from_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, calls: list[dict[str, Any]]
) -> None:
    home = tmp_path / "home-with-desktop"
    home.mkdir()
    (home / "desktop.url").write_text("http://127.0.0.1:43127", encoding="utf-8")
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    result = runner.invoke(app, ["api", "GET", "/api/health"])
    assert result.exit_code == 0, result.stdout
    assert [c["url"] for c in _requests(calls)] == ["http://127.0.0.1:43127/api/health"]


def test_api_reports_server_unavailable_without_request_secrets(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]
) -> None:
    _use_token(monkeypatch)

    def offline() -> httpx.Response:
        raise httpx.ConnectError(f"offline; Authorization: Bearer {TOKEN}")

    monkeypatch.setattr(apilogs.httpx, "Client", _FakeClient(calls, offline))
    result = runner.invoke(app, ["api", "GET", "/api/health"])
    assert result.exit_code == 1
    assert "Cannot reach Chimera server" in result.stdout
    assert "offline" not in result.stdout
    assert TOKEN not in result.stdout


def test_api_refuses_to_send_the_token_over_remote_plain_http(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]
) -> None:
    _use_token(monkeypatch)
    result = runner.invoke(app, ["api", "GET", "/api/health", "--url", "http://203.0.113.5:8765"])
    assert result.exit_code == 2
    assert "Refusing to send CHIMERA_SERVER_TOKEN" in result.stdout
    assert TOKEN not in result.stdout
    assert calls == []


def test_api_sends_the_token_to_a_remote_https_url(
    monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]
) -> None:
    _use_token(monkeypatch)
    result = runner.invoke(app, ["api", "GET", "/api/health", "--url", "https://chimera.example"])
    assert result.exit_code == 0, result.stdout
    assert _requests(calls)[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    # Remote: the environment's proxy and CA settings are the person's to apply.
    assert _clients(calls) == [{"trust_env": True}]


@pytest.mark.parametrize(
    "base", ["http://127.0.0.1:8765", "http://localhost:9000", "http://[::1]:8765"]
)
def test_api_ignores_proxy_environment_on_loopback(
    base: str, monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]
) -> None:
    """With HTTP_PROXY set, a local call must not leave the machine with the token on it."""
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    result = runner.invoke(app, ["api", "GET", "/api/health", "--url", base])
    assert result.exit_code == 0, result.stdout
    assert _clients(calls) == [{"trust_env": False}]


def _write_log(tmp_path: Path, text: str) -> tuple[Path, Path]:
    home = tmp_path / "home"
    log = home / "logs" / "backend.log"
    log.parent.mkdir(parents=True)
    log.write_text(text, encoding="utf-8")
    return home, log


@pytest.fixture
def log_home(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[Path], None]]:
    def use(home: Path) -> None:
        monkeypatch.setenv("CHIMERA_HOME", str(home))
        get_settings.cache_clear()

    yield use
    get_settings.cache_clear()


def test_logs_filters_by_run_and_minimum_level(
    tmp_path: Path, log_home: Callable[[Path], None]
) -> None:
    home, _ = _write_log(
        tmp_path,
        "2026-10-15 10:00:00 INFO chimera: run_id=run-a started\n"
        "2026-10-15 10:00:01 ERROR chimera: run_id=run-a failed\n"
        "Traceback (most recent call last):\n"
        "  ValueError: the cause of run-a's failure\n"
        "2026-10-15 10:00:02 ERROR chimera: run_id=run-b failed\n"
        "Traceback (most recent call last):\n"
        "  KeyError: the cause of the other failure\n",
    )
    log_home(home)
    result = runner.invoke(app, ["logs", "--run", "run-a", "--level", "ERROR"])
    assert result.exit_code == 0, result.stdout
    assert "run-a failed" in result.stdout
    assert "started" not in result.stdout
    assert "run-b" not in result.stdout
    # A traceback has neither level nor run id; it follows its record, in both directions.
    assert "the cause of run-a's failure" in result.stdout
    assert "the cause of the other failure" not in result.stdout


def test_logs_lines_keeps_the_last_matching_records(
    tmp_path: Path, log_home: Callable[[Path], None]
) -> None:
    home, _ = _write_log(
        tmp_path,
        "".join(f"2026-10-15 10:00:0{i} WARNING chimera: entry-{i}\n" for i in range(6)),
    )
    log_home(home)
    result = runner.invoke(app, ["logs", "--lines", "2"])
    assert result.exit_code == 0, result.stdout
    assert [line for line in result.stdout.splitlines() if line] == [
        "2026-10-15 10:00:04 WARNING chimera: entry-4",
        "2026-10-15 10:00:05 WARNING chimera: entry-5",
    ]


def test_logs_follow_prints_new_records_and_survives_rotation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, log_home: Callable[[Path], None]
) -> None:
    home, log = _write_log(
        tmp_path, "2026-10-15 10:00:00 WARNING chimera: history " + "x" * 200 + "\n"
    )
    log_home(home)

    def append() -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write("2026-10-15 10:00:01 ERROR chimera: appended\n")

    def rotate() -> None:
        # The writer rotated: the file starts over, shorter than where the tail stood.
        log.write_text("2026-10-15 10:00:02 ERROR chimera: after rotation\n", encoding="utf-8")

    steps: Iterator[Callable[[], None]] = iter([append, rotate])

    def sleep(_seconds: float) -> None:
        step = next(steps, None)
        if step is None:
            raise KeyboardInterrupt  # what Ctrl+C does to a real follow
        step()

    monkeypatch.setattr(apilogs.time, "sleep", sleep)
    result = runner.invoke(app, ["logs", "--follow", "--lines", "1"])
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert out.count("history") == 1, "history is printed once, not again by the follow"
    assert out.index("history") < out.index("appended") < out.index("after rotation")


def test_logs_explains_when_backend_log_is_missing(
    tmp_path: Path, log_home: Callable[[Path], None]
) -> None:
    home = tmp_path / "home-without-log-and-a-long-enough-name-to-pass-eighty-columns"
    log_home(home)
    result = runner.invoke(app, ["logs"])
    assert result.exit_code == 1
    # Whole, on one line: a wrapped path cannot be copied into the next command.
    assert str(home / "logs" / "backend.log") in result.stdout
    assert "chimera serve" in result.stdout
    assert "CHIMERA_HOME" in result.stdout


def test_serve_writes_the_backend_log_the_logs_command_reads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`chimera logs` names `chimera serve` as a writer of the file; that has to be true."""
    from chimera import file_log
    from chimera.cli.commands import serve as serve_cmds

    home = tmp_path / "serve-home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_OPENROUTER_API_KEY", "test-key")  # `serve` exits without one
    get_settings.cache_clear()
    attached: list[Path] = []
    monkeypatch.setattr(file_log, "attach_file_log", lambda path: attached.append(path) or path)
    monkeypatch.setattr(serve_cmds, "_serve_mcp", lambda *args, **kwargs: None)
    try:
        result = runner.invoke(app, ["serve", "--mcp", "--workspace", str(tmp_path)])
    finally:
        get_settings.cache_clear()
    assert result.exit_code == 0, result.stdout
    assert attached == [home / "logs" / "backend.log"]
