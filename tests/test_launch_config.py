"""`.chimera/launch.json` — the only thing that can name a dev server to start.

What each test would catch if the rule broke: an unknown field or a wrong type being read leniently (a
typo silently starting nothing), an outside server being previewed, a `cwd` leaving the project, a
secret in `env` going unmentioned, Claude Code's own file not being read, and an INVALID file being
treated as a valid one (starting whatever parsed).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.core.launch_config import (
    load_launch_config,
    parse_launch_config,
    resolve_cwd,
)


def _ok(**entry: Any) -> dict[str, Any]:
    return {"version": "0.0.1", "configurations": [{"name": "web", "runtimeExecutable": "npm", **entry}]}


def _parse(data: dict[str, Any]) -> tuple[Any, list[str]]:
    return parse_launch_config(json.dumps(data))


def test_a_minimal_recipe_gets_the_documented_defaults() -> None:
    config, errors = _parse({"configurations": [{"name": "web", "runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"]}]})
    assert errors == [] and config is not None
    assert config.autoVerify is True  # on unless the file says otherwise
    entry = config.configurations[0]
    assert entry.port == 3000 and entry.autoPort is None and entry.env == {}
    assert entry.argv(3001) == ["npm", "run", "dev"]


def test_auto_verify_can_be_switched_off_in_the_file() -> None:
    config, _ = _parse({"autoVerify": False, **_ok()})
    assert config is not None and config.autoVerify is False


def test_port_is_substituted_in_the_arguments_and_never_goes_through_a_shell() -> None:
    config, _ = _parse(_ok(runtimeArgs=["run", "dev", "--", "--port", "${PORT}"]))
    assert config is not None
    assert config.configurations[0].argv(4321) == ["npm", "run", "dev", "--", "--port", "4321"]


def test_program_and_args_are_node_sugar() -> None:
    config, errors = _parse({"configurations": [{"name": "api", "program": "server.js", "args": ["--x"], "port": 8080}]})
    assert errors == [] and config is not None
    assert config.configurations[0].argv(8080) == ["node", "server.js", "--x"]


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "web", "runtimeExecutable": "npm", "program": "a.js"},  # two spellings
        {"name": "web", "runtimeExecutable": "npm", "args": ["x"]},  # args without program
        {"name": "web"},  # nothing to run
        {"name": "web", "runtimeExecutable": "npm && curl evil"},  # shell syntax in the executable
        {"name": "web", "runtimeExecutable": "npm", "port": 0},
        {"name": "web", "runtimeExecutable": "npm", "port": 70000},
        {"name": "web", "runtimeExecutable": "npm", "surprise": True},  # unknown field
        {"name": "../x", "runtimeExecutable": "npm"},  # a name is a label, not a path
        {"name": "web", "runtimeExecutable": "npm", "env": {"A": 1}},  # env is strings
        {"name": "web", "runtimeExecutable": "npm", "runtimeArgs": "run dev"},  # a list, not a string
    ],
)
def test_an_invalid_entry_makes_the_whole_file_invalid(entry: dict[str, Any]) -> None:
    config, errors = _parse({"configurations": [entry]})
    assert config is None and errors


def test_unknown_top_level_fields_and_non_objects_are_refused() -> None:
    assert _parse({"configurations": [], "extra": 1})[0] is None
    assert parse_launch_config("[]")[0] is None
    config, errors = parse_launch_config("{not json")
    assert config is None and "JSON" in errors[0]


def test_names_must_be_unique_case_insensitively() -> None:
    config, errors = _parse({"configurations": [
        {"name": "web", "runtimeExecutable": "npm"}, {"name": "WEB", "runtimeExecutable": "yarn"},
    ]})
    assert config is None and any("duplicate" in e for e in errors)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",  # not this computer
        "http://192.168.1.5:3000",  # a LAN address is not loopback
        "http://user:pw@localhost:3000",  # credentials in the URL
        "http://localhost:3000/app",  # origin only
        "http://localhost:3000?x=1",
        "ftp://localhost:3000",
        "http://localhost:9999",  # port must be the entry's
    ],
)
def test_a_url_must_be_the_origin_of_a_loopback_http_server(url: str) -> None:
    config, errors = _parse({"configurations": [{"name": "ext", "url": url, "port": 3000}]})
    assert config is None and errors


@pytest.mark.parametrize("url", ["http://localhost:3000", "https://127.0.0.1:3000/", "http://[::1]:3000", "http://localhost"])
def test_a_loopback_url_is_a_server_already_running(url: str) -> None:
    config, errors = _parse({"configurations": [{"name": "ext", "url": url, "port": 3000}]})
    assert errors == [] and config is not None
    assert config.configurations[0].attach_only is True


def test_a_url_cannot_carry_a_command() -> None:
    config, _ = _parse({"configurations": [{"name": "x", "url": "http://localhost:3000", "runtimeExecutable": "npm"}]})
    assert config is None


def test_a_secret_in_env_is_a_warning_and_not_an_error(tmp_path: Path) -> None:
    (tmp_path / ".chimera").mkdir()
    (tmp_path / ".chimera" / "launch.json").write_text(
        json.dumps(_ok(env={"NODE_ENV": "development", "STRIPE_SECRET_KEY": "sk_live_1234567890"})), encoding="utf-8",
    )
    found = load_launch_config(tmp_path)
    assert found.config is not None and found.errors == ()
    assert len(found.warnings) == 1 and "STRIPE_SECRET_KEY" in found.warnings[0]
    assert "sk_live_1234567890" not in found.warnings[0]  # the value is never repeated


def test_cwd_stays_inside_the_project(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    assert resolve_cwd(tmp_path, None) == tmp_path.resolve()
    assert resolve_cwd(tmp_path, "${workspaceFolder}/web") == (tmp_path / "web").resolve()
    assert resolve_cwd(tmp_path, "web") == (tmp_path / "web").resolve()
    assert resolve_cwd(tmp_path, "..") is None
    assert resolve_cwd(tmp_path, str(tmp_path.parent)) is None


def test_a_cwd_outside_the_project_makes_the_file_invalid(tmp_path: Path) -> None:
    (tmp_path / ".chimera").mkdir()
    (tmp_path / ".chimera" / "launch.json").write_text(json.dumps(_ok(cwd="../elsewhere")), encoding="utf-8")
    found = load_launch_config(tmp_path)
    assert found.config is None and found.found and "outside the project" in found.errors[0]


def test_our_file_wins_and_claude_codes_is_the_fallback(tmp_path: Path) -> None:
    assert load_launch_config(tmp_path).found is False
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "launch.json").write_text(json.dumps(_ok()), encoding="utf-8")
    fallback = load_launch_config(tmp_path)
    assert fallback.config is not None and fallback.source == tmp_path / ".claude" / "launch.json"

    (tmp_path / ".chimera").mkdir()
    (tmp_path / ".chimera" / "launch.json").write_text(
        json.dumps({"configurations": [{"name": "ours", "runtimeExecutable": "pnpm"}]}), encoding="utf-8",
    )
    ours = load_launch_config(tmp_path)
    assert ours.config is not None and ours.config.configurations[0].name == "ours"


def test_an_invalid_first_file_is_not_replaced_by_the_second(tmp_path: Path) -> None:
    (tmp_path / ".chimera").mkdir()
    (tmp_path / ".chimera" / "launch.json").write_text("{broken", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "launch.json").write_text(json.dumps(_ok()), encoding="utf-8")
    found = load_launch_config(tmp_path)
    assert found.config is None and found.errors and found.source == tmp_path / ".chimera" / "launch.json"


def test_a_file_with_a_byte_order_mark_is_read(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "launch.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(_ok()).encode())
    assert load_launch_config(tmp_path).config is not None


@pytest.mark.parametrize(
    "name",
    ["NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "PATH", "Path", "BASH_ENV", "PYTHONSTARTUP",
     "npm_config_script_shell", "PORT", "JAVA_TOOL_OPTIONS", "ld_library_path"],
)
def test_env_that_changes_what_a_program_loads_or_runs_is_refused(name: str) -> None:
    config, errors = _parse(_ok(env={name: "x"}))
    assert config is None and any("cannot be set" in e for e in errors)


@pytest.mark.parametrize("value", ["a b", "$(id)", "x;y", "`id`", "a\nb", "x&y", "a|b", "'q'"])
def test_env_values_a_command_line_cannot_carry_are_refused(value: str) -> None:
    config, errors = _parse(_ok(env={"GREETING": value}))
    assert config is None and errors


def test_plain_env_is_kept() -> None:
    config, errors = _parse(_ok(env={"NODE_ENV": "development", "API_URL": "http://localhost:4000/v1"}))
    assert errors == [] and config is not None and config.configurations[0].env["NODE_ENV"] == "development"


def test_the_files_an_entry_runs_are_known_so_a_change_to_them_can_be_noticed(tmp_path: Path) -> None:
    from chimera.core.launch_config import executed_files

    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "server.js").write_text("x", encoding="utf-8")
    (tmp_path / "web" / "package.json").write_text("{}", encoding="utf-8")
    node, _ = _parse({"configurations": [{"name": "api", "program": "server.js", "cwd": "web"}]})
    assert [p.name for p in executed_files(tmp_path, node.configurations[0])] == ["server.js"]
    npm, _ = _parse({"configurations": [{"name": "w", "runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"], "cwd": "web"}]})
    assert (tmp_path / "web" / "package.json").resolve() in executed_files(tmp_path, npm.configurations[0])


def test_tool_configs_env_files_and_server_scripts_are_watched_too(tmp_path: Path) -> None:
    from chimera.core.launch_config import executed_files

    (tmp_path / "app").mkdir()
    names = [
        "vite.config.ts", "tailwind.config.js", "webpack.dev.js", "next.config.mjs", "nuxt.config.ts",
        "server.js", ".env", ".env.local",
    ]
    for name in names:
        (tmp_path / "app" / name).write_text("x", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("x", encoding="utf-8")
    (tmp_path / "postcss.config.cjs").write_text("x", encoding="utf-8")  # the root counts as well
    config, _ = _parse({"configurations": [{"name": "w", "runtimeExecutable": "npm", "runtimeArgs": ["run", "dev"], "cwd": "app"}]})
    watched = {p.name for p in executed_files(tmp_path, config.configurations[0])}
    assert set(names) <= watched and "postcss.config.cjs" in watched and "unrelated.txt" not in watched
