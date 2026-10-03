"""``/api/storage`` and ``/api/diagnostics`` (study 29, P5.3).

Two promises. The storage actions remove files, so each one is refused unless the request says
``confirm: true`` in so many words — a stray call from anything that can reach the API does nothing.
And the diagnostics show the backend's crash report, which is its stderr: a provider error there
quotes the request it failed on, key included. Everything shown is scrubbed first.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings

SECRET = "zq-this-is-the-owners-real-key-0123456789"


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", "")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client_for(home: Path, tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")  # type: ignore[call-arg]
    # The factory is never called: these routes build no conversation.
    return TestClient(build_api_app(lambda: None, workspace=ws, settings=settings))  # type: ignore[arg-type]


def test_the_storage_route_reports_every_category_with_null_for_the_unmeasured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")
    home = tmp_path / "home"
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / "s.json").write_text("{}", encoding="utf-8")

    body = client_for(home, tmp_path).get("/api/storage").json()

    from chimera.core.storage import CATEGORY_KEYS

    assert [row["key"] for row in body["categories"]] == list(CATEGORY_KEYS)
    rows = {row["key"]: row for row in body["categories"]}
    assert rows["sessions"]["bytes"] == 2
    assert rows["browsers"]["bytes"] is None, "an unknown location is null, not zero"
    assert body["rotatable_logs"] == ["traces.jsonl", "scheduler/cron_traces.jsonl"]


@pytest.mark.parametrize(
    "route", ["/api/storage/worktrees/prune", "/api/storage/logs/rotate"]
)
@pytest.mark.parametrize("body", [{}, {"confirm": False}])
def test_an_action_without_confirm_is_refused_and_removes_nothing(
    route: str, body: dict[str, bool], tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "traces.jsonl").write_text("x\n", encoding="utf-8")
    orphan = Path(tempfile.mkdtemp(prefix="chimera-wt-"))
    import os
    import time

    old = time.time() - 3 * 3600
    os.utime(orphan, (old, old))

    response = client_for(home, tmp_path).post(route, json=body)

    assert response.status_code == 400
    assert "confirm" in response.json()["detail"]
    assert orphan.exists() and (home / "traces.jsonl").exists()


def test_a_confirmed_prune_and_rotation_do_what_they_say(tmp_path: Path) -> None:
    import os
    import time

    home = tmp_path / "home"
    home.mkdir()
    (home / "traces.jsonl").write_text("x\n", encoding="utf-8")
    orphan = Path(tempfile.mkdtemp(prefix="chimera-wt-"))
    old = time.time() - 3 * 3600
    os.utime(orphan, (old, old))
    client = client_for(home, tmp_path)

    pruned = client.post("/api/storage/worktrees/prune", json={"confirm": True}).json()
    rotated = client.post("/api/storage/logs/rotate", json={"confirm": True}).json()

    assert pruned["removed"] == 1 and not orphan.exists()
    assert rotated == {"rotated": 1, "bytes_freed": 0, "failed": 0}
    assert (home / "traces.jsonl.1").exists()


def desktop_layout(tmp_path: Path, crash: str) -> Path:
    """The desktop's own layout: `<data dir>/data` is the home, the crash report sits beside it."""
    data_dir = tmp_path / "app-data"
    home = data_dir / "data"
    home.mkdir(parents=True)
    (data_dir / "backend-crash.txt").write_text(crash, encoding="utf-8")
    return home


def test_the_crash_report_is_shown_with_its_credentials_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOMEPROVIDER_API_KEY", SECRET)
    crash = (
        "Chimera desktop — backend stopped while the app was running\n"
        "version: 0.64.1\n"
        f"litellm.AuthenticationError: invalid key {SECRET}\n"
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789\n"
        "OTHER_SERVICE_TOKEN=a-token-this-process-never-held-9876\n"
        "GET https://api.example.com/v1/x?api_key=leaked-in-a-url-55555\n"
        "Traceback (most recent call last): ValueError: boom\n"
    )
    home = desktop_layout(tmp_path, crash)

    body = client_for(home, tmp_path).get("/api/diagnostics").json()

    shown = body["crash"]["text"] + "\n" + body["report"]
    for leaked in (
        SECRET,
        "abcdefghijklmnopqrstuvwxyz0123456789",
        "a-token-this-process-never-held-9876",
        "leaked-in-a-url-55555",
    ):
        assert leaked not in shown, leaked
    # Scrubbed, not emptied: the part that diagnoses the crash is still there.
    assert "ValueError: boom" in body["crash"]["text"]
    assert "backend stopped" in body["report"]
    assert body["crash"]["path"].endswith("backend-crash.txt")


def test_outside_the_desktop_layout_no_crash_file_is_read(tmp_path: Path) -> None:
    """A CLI home's parent is somebody's project; a file there that happens to have this name is not
    the desktop's crash report."""
    home = tmp_path / "project" / ".chimera"
    home.mkdir(parents=True)
    (home.parent / "backend-crash.txt").write_text("not ours", encoding="utf-8")

    body = client_for(home, tmp_path).get("/api/diagnostics").json()

    assert body["crash"] is None
    assert "not ours" not in body["report"]


def test_the_diagnostics_name_the_paths_and_the_version(tmp_path: Path) -> None:
    from chimera import __version__

    home = tmp_path / "home"
    body = client_for(home, tmp_path).get("/api/diagnostics").json()

    assert body["backend_version"] == __version__
    assert Path(body["home"]) == home.resolve()
    assert Path(body["workspace"]) == (tmp_path / "ws").resolve()
    assert Path(body["worktree_dir"]) == Path(tempfile.gettempdir())
    assert "storage" in body["report"] and f"backend version: {__version__}" in body["report"]


def test_the_worktree_folder_is_a_setting_the_app_can_save_and_read_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.config_api import patch_config, read_config

    monkeypatch.delenv("CHIMERA_WORKTREE_DIR", raising=False)
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="absolute"):
        patch_config({"CHIMERA_WORKTREE_DIR": "relative/wts"}, env_path=env)
    target = str(tmp_path / "wts")
    patch_config({"CHIMERA_WORKTREE_DIR": target}, env_path=env)
    assert read_config(get_settings())["storage"]["worktree_dir"] == target
    patch_config({"CHIMERA_WORKTREE_DIR": ""}, env_path=env)
    assert read_config(get_settings())["storage"]["worktree_dir"] == ""


def test_the_doctor_prints_the_same_storage_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-que-nao-vale-nada")
    get_settings.cache_clear()

    out = CliRunner().invoke(app, ["doctor"]).output

    assert "Storage" in out
    for label in ("sessions", "memory", "worktrees", "worktree location"):
        assert label in out, label


def test_a_worktree_folder_inside_the_project_is_refused_at_the_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: the save only asked for an absolute path, so `<project>/wts` was stored — and
    then passed over for temp by every run in that project. A setting that silently does nothing is
    refused where the owner is looking: at the save."""
    from chimera.api.config_api import patch_config

    monkeypatch.delenv("CHIMERA_WORKTREE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # the route writes `.env` in the working directory
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(ValueError, match="inside the project"):
        patch_config({"CHIMERA_WORKTREE_DIR": str(ws / "wts")}, env_path=tmp_path / ".env", workspace=ws)

    client = client_for(tmp_path / "home", tmp_path)
    refused = client.patch("/api/config", json={"CHIMERA_WORKTREE_DIR": str(ws / "wts")})
    assert refused.status_code == 400 and "inside the project" in refused.json()["detail"]
    accepted = client.patch("/api/config", json={"CHIMERA_WORKTREE_DIR": str(tmp_path / "outside")})
    assert accepted.status_code == 200


@pytest.mark.parametrize(
    ("line", "secret"),
    [
        ('{"api_key": "plainvalue-0123456789abcdef"}', "plainvalue-0123456789abcdef"),
        ("{'x-api-key': 'anthropicvalue-0123456789'}", "anthropicvalue-0123456789"),
        ("password=hunter2-correct-horse", "hunter2-correct-horse"),
        ("openai_api_key='lowercase-value-0123456789'", "lowercase-value-0123456789"),
        ("Api-Key: MixedCaseValue0123456789", "MixedCaseValue0123456789"),
        ('{"client_secret": "a secret with spaces in it"}', "a secret with spaces"),
        ("key AIzaSyA1234567890abcdefghijklmnopqrstuv in a url", "AIzaSyA1234567890abcdefghijklmnopqrstuv"),
        (
            "GET https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getMe",
            "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
        ),
    ],
)
def test_credentials_this_process_never_held_are_scrubbed_in_every_known_format(
    line: str, secret: str
) -> None:
    """Review finding: the net masked only UPPER_CASE `NAME=value`, so a key rotated since the crash,
    or one from an MCP server's config — not in this environment — passed intact in JSON, in a dict's
    repr (the Anthropic client's headers), in lowercase, and in the Google and Telegram shapes. The
    text is what a person pastes into a public issue, after a screen told them it was safe."""
    from chimera.api.storage_api import scrub

    shown = scrub(f"provider error: {line}\n")
    assert secret not in shown, shown
    assert "[redacted]" in shown and "provider error" in shown


def test_the_scrub_leaves_the_numbers_that_diagnose_a_provider_error() -> None:
    from chimera.api.storage_api import scrub

    line = "max_tokens=4096 prompt_tokens: 123 tokenizer: gpt2 api_key=[redacted]"
    assert scrub(line) == line
