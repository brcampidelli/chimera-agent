"""The bridge may not write the settings that set its limits (study 29, P5.5 and after).

P5.5 made the sharing switch and the link expiry the owner's: the Settings screen writes them, the
desktop bridge — the tier external agents use over MCP — may not, full control or not. The same
reasoning was true of far more of the allowlist, and nothing held it there. With full control a
client could still widen the browser's reach (P5.2: an emptied site list is ANY public site), give
the sandbox a network (P5.4), loosen OpenRouter's privacy fences (P5.6), and — before any of those —
set its own posture, switch the trust kernel off, empty the denylist, or point the approval webhook
at itself.

The rule: whatever widens what the agent can reach, loosens a guard or a privacy fence, or changes
who answers an approval is the owner's. What is pinned here:

* every owner-only key is refused through the bridge with full control on, with the route's own
  refusal (403, ``not editable through the bridge: KEY``), and nothing reaches ``.env`` or the
  process environment — and the owner's own ``PATCH /api/config`` still saves the same value;
* every key in ``OWNER_ONLY_SETTINGS`` is a setting the endpoint really edits (a typo would be a
  key that protects nothing);
* the WHOLE editable allowlist is classified three ways — owner-only and refused flat, owner-only
  but suggestable (the model choices and the scheduler's switch, since 2026-10-04: the bridge
  leaves the owner a card and writes nothing, see
  `test_the_bridge_may_only_suggest_which_model_answers.py`), or bridge-writable — so a setting
  added later has to be put on one side on purpose;
* a body mixing an owner-only key with a writable one is refused whole, and so is a body mixing a
  suggestable key with a writable one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import OWNER_ONLY_SETTINGS, SUGGESTABLE_SETTINGS, is_secret_setting
from chimera.api.config_api import _EDITABLE_SETTINGS, _SECRET_KEYS, is_editable
from chimera.config import get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65004"

#: Each owner-only key: the value it starts at, and the value a client would want — the widening
#: (or loosening) direction. The owner's PATCH saves the second; the bridge's is refused.
OWNER_ONLY: dict[str, tuple[str, str]] = {
    # The bridge's own switches (P5.5): a client that could write them widens its own access.
    "CHIMERA_DESKTOP_BRIDGE": ("true", "false"),
    "CHIMERA_DESKTOP_BRIDGE_FULL": ("true", "false"),
    # Sharing (P5.5).
    "CHIMERA_SHARING": ("false", "true"),
    "CHIMERA_SHARE_EXPIRY_HOURS": ("1", ""),
    # Posture, guards, approvals.
    "CHIMERA_REACH": ("read_only", "workspace_shell"),
    "CHIMERA_APPROVAL": ("always", "never"),
    "CHIMERA_HOST_EXEC": ("deny", "allow"),
    "CHIMERA_GOVERNANCE": ("enforce", "off"),
    "CHIMERA_TOOL_DENYLIST": ("run_shell", ""),
    "CHIMERA_GUARD_CHAT": ("true", "false"),
    "CHIMERA_APPROVAL_WEBHOOK": ("", "https://hook.example.invalid/answer"),
    "CHIMERA_DECISION_BACKEND": ("local_logprob", "hosted_verbalized"),
    "CHIMERA_DECISION_MODEL": ("qwen3:4b", ""),
    "CHIMERA_DAILY_USD_CAP": ("1", ""),
    # Reach (P5.2, P5.4).
    "CHIMERA_SANDBOX": ("docker", "local"),
    "CHIMERA_SANDBOX_NETWORK": ("none", "bridge"),
    "CHIMERA_EGRESS_ALLOW": ("", "example.com"),
    "CHIMERA_BROWSER_SITES": ("example.com", ""),
    "CHIMERA_BROWSER_LOCAL_PORTS": ("", "5173"),
    "CHIMERA_MCP_AUTOLOAD": ("false", "true"),
    # Study 29, phases 6-8: a tool that publishes the owner's code to a remote, and the branch
    # prefix the worktree cleanup force-deletes under.
    "CHIMERA_PULL_REQUESTS": ("false", "true"),
    "CHIMERA_BRANCH_PREFIX": ("chimera", "feature"),
    # The project pack narrows (P7.6): switching it off hands back what the pack took away.
    "CHIMERA_PROJECT_PACK": ("true", "false"),
    # Study 30, S30-27 and S30-28: each only adds questions, so off is the widening direction.
    "CHIMERA_EXFIL_HOST_PATH": ("true", "false"),
    "CHIMERA_SHELL_FETCH_GUARD": ("true", "false"),
    # Who may reach the agent: an empty list is anyone.
    "CHIMERA_APP_MESSAGING": ("false", "true"),
    "CHIMERA_DISCORD_ALLOWED_USERS": ("111", ""),
    "CHIMERA_TELEGRAM_ALLOWED_USERS": ("222", ""),
    "CHIMERA_SLACK_ALLOWED_USERS": ("U333", ""),
    "CHIMERA_SIGNAL_ALLOWED_USERS": ("+15550000001", ""),
    "CHIMERA_WHATSAPP_ALLOWED_NUMBERS": ("+15550000002", ""),
    # What the bots carry out: the files a turn wrote, sent to a Discord channel (P6.3).
    "CHIMERA_DISCORD_ATTACH_FILES": ("false", "true"),
    # Where prompts may go (P5.6, and the three base URLs).
    "CHIMERA_OPENROUTER_DATA_COLLECTION": ("deny", "allow"),
    "CHIMERA_OPENROUTER_ZDR": ("true", "false"),
    "CHIMERA_API_BASE": ("", "https://api.example.invalid/v1"),
    "CHIMERA_OLLAMA_BASE_URL": ("http://127.0.0.1:11434", "http://ollama.example.invalid:11434"),
    "CHIMERA_LM_STUDIO_BASE_URL": ("http://127.0.0.1:1234/v1", "http://lms.example.invalid:1234/v1"),
    # Where the owner's keys live (P7.7): off sends the next key typed into a plain-text file.
    "CHIMERA_KEY_VAULT": ("true", "false"),
    # Whether the agent's read tools may read Chimera's own .env (owner's decision, 2026-10-04):
    # on puts the provider keys within the model's reach, so a client may not turn it back on.
    "CHIMERA_AGENT_READS_OWN_ENV": ("false", "true"),
}

#: The settings the bridge may only SUGGEST (owner's decision, 2026-10-04): which model or route a
#: prompt goes to, and whether the app runs scheduled jobs. Each with the value it starts at and the
#: value a client proposes; the round trip — card, owner's yes, exactly that value written — is held
#: in `test_the_bridge_may_only_suggest_which_model_answers.py`.
SUGGESTABLE: dict[str, tuple[str, str]] = {
    "CHIMERA_DEFAULT_MODEL": ("openrouter/vendor/before", "openrouter/vendor/after"),
    "CHIMERA_WEAK_MODEL": ("openrouter/vendor/weak-a", "openrouter/vendor/weak-b"),
    "CHIMERA_MID_MODEL": ("openrouter/vendor/mid-a", "openrouter/vendor/mid-b"),
    "CHIMERA_ORCHESTRATOR_MODEL": ("openrouter/vendor/orch-a", "openrouter/vendor/orch-b"),
    "CHIMERA_FALLBACK_MODELS": ("openrouter/a/one", "openrouter/b/two,openrouter/c/three"),
    "CHIMERA_EMBED_MODEL": ("openrouter/openai/embed-a", "ollama/nomic-embed-text"),
    "CHIMERA_COMPLETE_MODEL": ("ollama/base-a", "ollama/base-b"),
    "CHIMERA_VOICE_MODEL": ("openrouter/vendor/voice-a", "openrouter/vendor/voice-b"),
    "CHIMERA_VOICE_WORK_MODEL": ("openrouter/vendor/work-a", "openrouter/vendor/work-b"),
    "CHIMERA_FUSION_PANEL": (
        "openrouter/a/one,openrouter/b/two",
        "openrouter/c/three,openrouter/d/four",
    ),
    "CHIMERA_FUSION_JUDGE": ("openrouter/judge/a", "openrouter/judge/b"),
    "CHIMERA_FUSION_SYNTHESIZER": ("openrouter/synth/a", "openrouter/synth/b"),
    "CHIMERA_COST_MODE": ("auto", "premium"),
    "CHIMERA_CASCADE": ("false", "true"),
    "CHIMERA_VERIFIED_ANSWERS": ("true", "false"),
    "CHIMERA_APP_CRON": ("false", "true"),
}

#: Every other editable setting, and why the bridge may keep writing it. None of these changes what a
#: run may reach, what judges it, who may command it, where its prompts go, or which model reads
#: them.
BRIDGE_WRITABLE: frozenset[str] = frozenset(
    {
        # Caches and memory: local stores, read by the same agent under the same posture.
        "CHIMERA_CACHE",
        "CHIMERA_PROMPT_CACHE",
        "CHIMERA_MEMORY_BACKEND",
        "CHIMERA_SEMANTIC_MEMORY",
        "CHIMERA_AUTO_CONSOLIDATE",
        "CHIMERA_CHAT_MEMORY",
        "CHIMERA_SKILL_CARDS",  # reads back skills, which only an approval publishes
        # Whether a job's channel hears that it failed, and the machine's sleep. (Whether the
        # scheduler runs at all, `CHIMERA_APP_CRON`, is suggestable only: what runs unattended is
        # the owner's to decide.)
        "CHIMERA_CRON_NOTIFY_FAILURES",
        "CHIMERA_KEEP_AWAKE",
        "CHIMERA_KEEP_AWAKE_ON_BATTERY",
        "CHIMERA_ARCHIVE_AFTER_DAYS",  # archives; deletes nothing
        # Display and experiments that add no reach: the browser's window, the context it is given,
        # the explorer's report format, and a research sub-agent with the read-only web tools the
        # Code turn already holds (`http_get` is in `default_registry`), inside the same ledger.
        "CHIMERA_BROWSER_HEADLESS",
        "CHIMERA_BROWSER_SITUATION",
        "CHIMERA_EXPLORER_CONTRACT",
        "CHIMERA_RESEARCH_AGENT",
        # Deferral changes how tools are DECLARED, not which may run: the proxy re-applies the
        # denylist and the allowlist itself (`chimera/tools/defer.py::_Deferred.permitted`).
        "CHIMERA_DEFER_TOOLS",
        "CHIMERA_MCP_DEFER",
        # The Tools screen's switches: each turns on a tool that works inside the reach above.
        "CHIMERA_EDIT_BATCH",
        "CHIMERA_TODO_LIST",
        "CHIMERA_DECIDE_TOOL",
        # `create_document` (P6.2) writes a Word, Excel, PowerPoint or PDF file only where
        # `write_file` may — through `resolve_for` and the run's write region — runs nothing the
        # model wrote and fetches nothing; the ledger counts it as a write tool like the others.
        "CHIMERA_CREATE_DOCUMENT",
        # The sandbox's image: the container keeps its network, mounts and limits, which are set by
        # `chimera/sandbox/docker.py` and the owner's keys, not by the image.
        "CHIMERA_SANDBOX_IMAGE",
        # Where worktrees are checked out: a copy of a project the run already reaches; a folder
        # inside the project is refused at the save.
        "CHIMERA_WORKTREE_DIR",
    }
)


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> Any:
    """A real app with the bridge on at full control. Every key a test writes is owned through
    ``monkeypatch`` first: ``patch_config`` sets ``os.environ`` for real."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _bridge_edit(client: TestClient, app: Any, body: dict[str, str]) -> Any:
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    return client.post(
        "/api/bridge/call", json={"route": "settings.edit", "body": body}, headers=headers
    )


def _env_file(tmp_path: Path) -> str:
    env = tmp_path / ".env"
    return env.read_text(encoding="utf-8") if env.exists() else ""


def test_every_owner_only_setting_is_one_the_settings_endpoint_really_edits() -> None:
    """A typo in the set would protect a key nobody writes, and leave the real one open."""
    unknown = sorted(k for k in OWNER_ONLY_SETTINGS if not is_editable(k))
    assert unknown == []
    # Settings, not credential slots: those are refused by `is_secret_setting` already.
    assert OWNER_ONLY_SETTINGS <= _EDITABLE_SETTINGS
    assert not OWNER_ONLY_SETTINGS & _SECRET_KEYS


def test_the_whole_allowlist_is_classified_flat_suggestable_or_bridge_writable() -> None:
    """A setting added to the allowlist has to be put on one side on purpose: refused flat, only
    suggested to the owner, or written by the bridge — and on exactly one."""
    assert set(OWNER_ONLY) == OWNER_ONLY_SETTINGS
    assert set(SUGGESTABLE) == SUGGESTABLE_SETTINGS
    assert not OWNER_ONLY_SETTINGS & BRIDGE_WRITABLE
    assert not SUGGESTABLE_SETTINGS & BRIDGE_WRITABLE
    assert not SUGGESTABLE_SETTINGS & OWNER_ONLY_SETTINGS
    unclassified = sorted(
        _EDITABLE_SETTINGS - OWNER_ONLY_SETTINGS - SUGGESTABLE_SETTINGS - BRIDGE_WRITABLE
    )
    assert unclassified == [], (
        "classify each: owner-only or suggestable (bridge_routes), or BRIDGE_WRITABLE here"
    )
    assert sorted(BRIDGE_WRITABLE - _EDITABLE_SETTINGS) == [], "not an editable setting"
    # Suggestable keys are settings, never credential slots: a card shows their values whole.
    assert SUGGESTABLE_SETTINGS <= _EDITABLE_SETTINGS
    assert not SUGGESTABLE_SETTINGS & _SECRET_KEYS
    assert not any(is_secret_setting(k) for k in SUGGESTABLE_SETTINGS)


def test_every_model_the_allowlist_holds_is_never_one_the_bridge_writes() -> None:
    """The audit, kept: any `*_MODEL(S)` the settings screen can write is either the owner's flat
    refusal (the decision model, the governance band's instrument) or suggestable — never one the
    bridge writes. A model setting added to the allowlist later is caught here."""
    models = {k for k in _EDITABLE_SETTINGS if k.endswith(("_MODEL", "_MODELS"))}
    assert len(models) >= 10, "probe is broken"
    assert not models & BRIDGE_WRITABLE
    assert models - OWNER_ONLY_SETTINGS <= SUGGESTABLE_SETTINGS


@pytest.mark.parametrize("key", sorted(OWNER_ONLY))
def test_the_bridge_with_full_control_may_not_write_it_and_the_owner_still_can(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial, widened = OWNER_ONLY[key]
    # The decision pair is checked as a pair against the other's current value.
    pair = {"CHIMERA_DECISION_BACKEND": "local_logprob", "CHIMERA_DECISION_MODEL": ""}
    app = _app(tmp_path, monkeypatch, **{**pair, key: initial})

    with TestClient(app) as client:
        refused = _bridge_edit(client, app, {key: widened})
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == f"not editable through the bridge: {key}"
        assert f"{key}=" not in _env_file(tmp_path)
        assert os.environ[key] == initial

        saved = client.patch("/api/config", json={key: widened})
    assert saved.status_code == 200, saved.text
    assert saved.json()["updated"] == [key]
    assert f"{key}={widened}" in _env_file(tmp_path).splitlines()
    assert os.environ[key] == widened
    get_settings.cache_clear()


def test_a_body_that_mixes_an_owner_setting_with_a_writable_one_is_refused_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(
        tmp_path,
        monkeypatch,
        CHIMERA_SANDBOX_IMAGE="before:image",
        CHIMERA_DEFAULT_MODEL="before/model",
        CHIMERA_REACH="read_only",
        CHIMERA_GOVERNANCE="enforce",
    )
    with TestClient(app) as client:
        body = {
            "CHIMERA_SANDBOX_IMAGE": "after:image",
            "CHIMERA_REACH": "workspace_shell",
            "CHIMERA_GOVERNANCE": "off",
        }
        refused = _bridge_edit(client, app, body)
        assert refused.status_code == 403
        # Every owner key the body named, so the client learns all of them in one answer.
        assert refused.json()["detail"] == (
            "not editable through the bridge: CHIMERA_GOVERNANCE, CHIMERA_REACH"
        )
        assert _env_file(tmp_path) == ""
        assert os.environ["CHIMERA_SANDBOX_IMAGE"] == "before:image"

        # A suggestable key mixed with a writable one: refused whole too, naming the suggestable
        # one — half written and half turned into a card would leave the client unsure which.
        mixed = _bridge_edit(
            client,
            app,
            {"CHIMERA_SANDBOX_IMAGE": "after:image", "CHIMERA_DEFAULT_MODEL": "after/model"},
        )
        assert mixed.status_code == 403, mixed.text
        assert mixed.json()["detail"].startswith(
            "only suggested to the owner through the bridge, never written: CHIMERA_DEFAULT_MODEL;"
        )
        assert _env_file(tmp_path) == ""
        assert os.environ["CHIMERA_DEFAULT_MODEL"] == "before/model"
        assert not list((tmp_path / "home" / "approvals").glob("*.ask.json"))

        # The writable one alone still goes through: full control operates the app.
        ok = _bridge_edit(client, app, {"CHIMERA_SANDBOX_IMAGE": "after:image"})
    assert ok.status_code == 200 and ok.json()["status"] == 200
    assert "CHIMERA_SANDBOX_IMAGE=after:image" in _env_file(tmp_path).splitlines()
    get_settings.cache_clear()
