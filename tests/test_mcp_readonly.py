"""Tests for rea alert selection and per-invocation reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.config import Settings
from chimera.governance.mcp_readonly import (
    REA_WRITE_TOOLS,
    alert_exec_tools,
    alert_rea_tools,
    exec_alert_names,
    rea_alert_names,
)
from chimera.tools.base import Tool, refusal
from chimera.tools.registry import ToolRegistry


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, CHIMERA_HOME=str(tmp_path / "home"))


def test_rea_write_tool_catalog_is_the_expected_set() -> None:
    assert frozenset(
        {
            "instrument_with_frida",
            "load_frida_script",
            "unload_frida_script",
            "start_frida_session",
            "resume_frida_session",
            "close_frida_session",
            "install_adb_package",
            "uninstall_adb_package",
            "push_adb_file",
            "start_adb_app",
            "start_adb_activity",
            "stop_adb_app",
            "capture_process_scenario",
            "capture_browser_scenario",
            "capture_electron_scenario",
            "capture_native_ui_scenario",
            "observe_native_calls",
            "observe_web_execution",
            "annotate_native_function",
            "set_comment",
            "set_inline_comment",
            "set_address_name",
            "set_addresses_names",
            "set_bookmark",
            "unset_bookmark",
        }
    ) == REA_WRITE_TOOLS


def test_alert_names_for_catalogue_name(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "mcp.json").write_text(
        json.dumps([{"name": "rea", "command": "npx", "args": ["-y", "rea"]}]),
        encoding="utf-8",
    )

    assert rea_alert_names(settings) == sorted(f"rea_{tool}" for tool in REA_WRITE_TOOLS)


def test_alert_names_without_rea_server(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "mcp.json").write_text(
        json.dumps([{"name": "other", "command": "node", "args": ["server.js"]}]),
        encoding="utf-8",
    )

    assert rea_alert_names(settings) == []

def test_alert_rea_tools_without_rea_returns_same_registry(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "mcp.json").write_text(
        json.dumps([{"name": "other", "command": "node", "args": ["server.js"]}]),
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.register(_FakeTool("ordinary_tool"))

    assert (
        alert_rea_tools(
            registry,
            settings=settings,
            surface="test",
            audit=_FakeAudit(),
        )
        is registry
    )


def test_alert_names_match_package_when_name_differs(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "mcp.json").write_text(
        json.dumps(
            [
                {
                    "name": "reverse-engineer",
                    "command": "npx",
                    "args": ["-y", "--package=rea-agents@6.4.0", "rea", "mcp"],
                }
            ]
        ),
        encoding="utf-8",
    )

    assert rea_alert_names(settings) == sorted(
        f"reverse-engineer_{tool}" for tool in REA_WRITE_TOOLS
    )


def test_alert_names_include_configured_rea_tools(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "mcp.json").write_text(
        json.dumps([{"name": "rea", "command": "npx", "args": ["-y", "rea"]}]),
        encoding="utf-8",
    )

    assert len(rea_alert_names(settings)) == 25
    assert all(name.startswith("rea_") for name in rea_alert_names(settings))


def test_rea_catalog_declares_no_auth_secrets() -> None:
    from chimera.integrations.mcp_catalog import CATALOG

    rea = next(entry for entry in CATALOG if entry.id == "rea")
    assert rea.auth == "none"
    assert rea.secrets == []
    assert len(rea.exec_tools) == 25
    assert rea.exec_tools == tuple(sorted(REA_WRITE_TOOLS))
    assert rea.match == "rea-agents"


class _FakeTool(Tool):
    def __init__(self, name: str, result: str = "ran") -> None:
        self.name = name
        self.description = "fake"
        self.parameters = {"type": "object", "properties": {}}
        self.result = result

    def run(self, **kwargs: object) -> str:
        return self.result


class _FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def record(self, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((event_type, payload))


def _configure_rea(settings: Settings) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "mcp.json").write_text(
        json.dumps([{"name": "rea", "command": "npx", "args": ["-y", "rea"]}]),
        encoding="utf-8",
    )


def test_unattended_surface_keeps_rea_tool_available_and_reports_each_call(
    settings: Settings,
) -> None:
    _configure_rea(settings)
    name = f"rea_{sorted(REA_WRITE_TOOLS)[0]}"
    registry = ToolRegistry()
    registry.register(_FakeTool(name))
    registry.register(_FakeTool("rea_read_analysis"))
    audit = _FakeAudit()
    alerts: list[str] = []

    wrapped = alert_rea_tools(
        registry, settings=settings, surface="cron:nightly", audit=audit, alert=alerts.append
    )

    assert name in wrapped.names()
    assert wrapped.run(name) == "ran"
    assert wrapped.run(name) == "ran"
    assert len(audit.events) == 2
    for event_type, payload in audit.events:
        assert event_type == "rea_tool_executed"
        assert payload["tool"] == name
        assert payload["surface"] == "cron:nightly"
        assert isinstance(payload["at"], str)
    assert len(alerts) == 2
    assert all(name in message and "cron:nightly" in message for message in alerts)
    assert wrapped.run("rea_read_analysis") == "ran"
    assert len(audit.events) == 2
    assert len(alerts) == 2

@pytest.mark.parametrize(
    "reason",
    [
        "[governance: BLOCKED — policy] The tool did NOT run.",
        "[governance: needs review — approval denied] The tool did NOT run.",
    ],
)
def test_refused_rea_tool_does_not_report_execution(settings: Settings, reason: str) -> None:
    _configure_rea(settings)
    name = f"rea_{sorted(REA_WRITE_TOOLS)[0]}"
    registry = ToolRegistry()
    registry.register(_FakeTool(name, result=refusal(reason)))
    audit = _FakeAudit()
    alerts: list[str] = []

    wrapped = alert_rea_tools(
        registry, settings=settings, surface="cron:nightly", audit=audit, alert=alerts.append
    )

    result = wrapped.run(name)

    assert result == refusal(reason)
    assert audit.events == []
    assert alerts == []



def _configure_servers(settings: Settings, servers: list[dict[str, object]]) -> None:
    settings.home.mkdir(parents=True, exist_ok=True)
    (settings.home / "mcp.json").write_text(json.dumps(servers), encoding="utf-8")


VOICESTUDIO_URL = "http://127.0.0.1:3900/mcp/"
VOICESTUDIO_HEADERS = {"X-OmniVoice-Client-Id": "chimera"}


def test_voicestudio_profile_creators_are_alerted_under_the_catalogue_name(settings: Settings) -> None:
    _configure_servers(
        settings,
        [{"name": "voicestudio", "url": VOICESTUDIO_URL, "headers": VOICESTUDIO_HEADERS}],
    )

    assert exec_alert_names(settings) == ["voicestudio_clone_voice", "voicestudio_design_voice"]


def test_a_local_url_server_is_matched_by_its_url_under_any_name(settings: Settings) -> None:
    _configure_servers(
        settings,
        [{"name": "my-voices", "url": VOICESTUDIO_URL, "headers": VOICESTUDIO_HEADERS}],
    )

    assert exec_alert_names(settings) == ["my-voices_clone_voice", "my-voices_design_voice"]


def test_a_different_url_under_a_voicestudio_name_is_not_matched(settings: Settings) -> None:
    _configure_servers(
        settings,
        [{"name": "other", "url": "http://127.0.0.1:4000/mcp/"}],
    )

    assert exec_alert_names(settings) == []


def test_voicestudio_tool_call_is_audited_under_the_neutral_event_and_alerted(settings: Settings) -> None:
    _configure_servers(
        settings,
        [{"name": "voicestudio", "url": VOICESTUDIO_URL, "headers": VOICESTUDIO_HEADERS}],
    )
    registry = ToolRegistry()
    registry.register(_FakeTool("voicestudio_clone_voice", result="cloned"))
    registry.register(_FakeTool("voicestudio_generate_speech"))
    audit = _FakeAudit()
    alerts: list[str] = []

    wrapped = alert_exec_tools(
        registry, settings=settings, surface="chat", audit=audit, alert=alerts.append
    )

    assert wrapped.run("voicestudio_clone_voice") == "cloned"
    assert wrapped.run("voicestudio_generate_speech") == "ran"
    assert len(audit.events) == 1
    event_type, payload = audit.events[0]
    assert event_type == "exec_tool_executed"
    assert payload["catalog_id"] == "voicestudio"
    assert payload["tool"] == "voicestudio_clone_voice"
    assert len(alerts) == 1
    assert "voicestudio_clone_voice" in alerts[0] and "voicestudio" in alerts[0]


def test_rea_still_records_its_documented_event_with_the_rea_catalogue_id(settings: Settings) -> None:
    _configure_rea(settings)
    name = f"rea_{sorted(REA_WRITE_TOOLS)[0]}"
    registry = ToolRegistry()
    registry.register(_FakeTool(name))
    audit = _FakeAudit()

    alert_exec_tools(registry, settings=settings, surface="chat", audit=audit, alert=lambda _m: None).run(name)

    event_type, payload = audit.events[0]
    assert event_type == "rea_tool_executed"
    assert payload["catalog_id"] == "rea"
