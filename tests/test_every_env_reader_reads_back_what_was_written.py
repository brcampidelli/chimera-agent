"""Every reader of `.env` reads back exactly what was written, and no other key moves.

The second adversarial review of 2026-10-04 (HIGH). The writers wrote every value bare after
``KEY=``, and python-dotenv does not read a bare value literally:

* a value opening a double quote (``"python:3.12-slim``) starts a quoted value that python-dotenv
  closes at the next quote, lines later — ONE bridge ``settings.edit`` of two keys it may write
  made dotenv stop seeing the owner's ``CHIMERA_REACH``, ``CHIMERA_APPROVAL``, ``CHIMERA_HOST_EXEC``
  and ``CHIMERA_TOOL_DENYLIST`` in between, which fell back to wider defaults;
* python-dotenv expands ``${NAME}`` in every value, quoted or not, with no escape, and
  pydantic-settings reads ``.env`` through it — so a written ``${STRIPE_SK}`` became that variable
  of the process on the next start, readable back through ``GET /api/config``.

The property held here, for every case of :data:`tests.env_value_cases.CASES` (API keys with ``$``,
quotes, backslashes and ``#`` among them) and every path that writes ``.env``: after the save, the
key's value read by python-dotenv, by pydantic-settings, by ``key_vault.read_env_values`` and — via
the shared fixture — by the desktop shell's Rust reader is byte-identical to what was written, and
every OTHER key reads as before. What no spelling can make safe is refused, everywhere: a
``${…}`` python-dotenv would expand, and a value that needs quotes and ends in a backslash.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, cast

import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from chimera.api import build_api_app
from chimera.api.config_api import patch_config
from chimera.api.key_vault import encode_env_value, read_env_values, write_env_value
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from tests.env_value_cases import CASES, FIXTURE, render

URL = "http://127.0.0.1:65012"
OWNER = (
    "CHIMERA_SANDBOX_IMAGE=python:3.12-slim\n"
    "CHIMERA_REACH=read_only\n"
    "CHIMERA_APPROVAL=always\n"
    "CHIMERA_HOST_EXEC=deny\n"
    "CHIMERA_TOOL_DENYLIST=run_shell,write_file\n"
    "CHIMERA_MEMORY_BACKEND=sqlite\n"
    "OTHER_HAND_WRITTEN='kept as is'\n"
)
OTHERS = {
    "CHIMERA_REACH": "read_only",
    "CHIMERA_APPROVAL": "always",
    "CHIMERA_HOST_EXEC": "deny",
    "CHIMERA_TOOL_DENYLIST": "run_shell,write_file",
}
REFUSED = [
    "img:${HOME}",
    "${STRIPE_SK}",
    "x${A:-default}y",
    "needs quotes #because of this and ends \\",
    " spaces at the ends and a backslash\\",
    "'leading quote and backslash\\",
]


class _FileOnly(Settings):
    """Settings reading ONE file and nothing else — what a restart would read from it."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (dotenv_settings,)


def _read_all(env: Path) -> dict[str, dict[str, Any]]:
    """The file as each reader sees it: python-dotenv, our own reader, and pydantic-settings."""
    monkey = {k: v for k, v in os.environ.items() if k == "PYTHON_DOTENV_DISABLED"}
    os.environ.pop("PYTHON_DOTENV_DISABLED", None)
    try:
        dot = {k: v for k, v in dotenv_values(env).items()}
        model = _FileOnly(_env_file=env)  # type: ignore[call-arg]
    finally:
        os.environ.update(monkey)
    return {"dotenv": dot, "ours": read_env_values(env.read_text(encoding="utf-8")), "model": {
        "CHIMERA_SANDBOX_IMAGE": model.sandbox_image,
        "CHIMERA_REACH": model.reach,
        "CHIMERA_APPROVAL": model.approval,
        "CHIMERA_HOST_EXEC": model.host_exec,
        "CHIMERA_TOOL_DENYLIST": ",".join(model.tool_denylist),
    }}


def _assert_round_trip(env: Path, key: str, value: str) -> None:
    seen = _read_all(env)
    assert seen["dotenv"][key] == value, ("dotenv", key, value)
    assert seen["ours"][key] == value, ("ours", key, value)
    if key == "CHIMERA_SANDBOX_IMAGE":
        assert seen["model"][key] == value, ("pydantic-settings", value)
    for other, expected in OTHERS.items():
        if other == key:
            continue
        assert seen["dotenv"][other] == expected, ("dotenv moved", other, value)
        assert seen["ours"][other] == expected, ("ours moved", other, value)
        assert seen["model"][other] == expected, ("pydantic-settings moved", other, value)
    assert seen["dotenv"]["OTHER_HAND_WRITTEN"] == "kept as is"


def test_the_fixture_the_shell_tests_read_is_what_the_encoder_writes() -> None:
    assert FIXTURE.read_bytes().decode("utf-8") == render(), (
        "regenerate: python -m tests.env_value_cases"
    )


@pytest.mark.parametrize("value", CASES)
def test_the_sink_writes_every_case_so_every_reader_reads_it_back(
    value: str, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text(OWNER, encoding="utf-8")
    write_env_value(env, "CHIMERA_SANDBOX_IMAGE", value)
    _assert_round_trip(env, "CHIMERA_SANDBOX_IMAGE", value)
    # And a later save of another key leaves it as it was.
    write_env_value(env, "CHIMERA_MEMORY_BACKEND", "json")
    _assert_round_trip(env, "CHIMERA_SANDBOX_IMAGE", value)


def test_every_function_that_writes_env_round_trips_every_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api.config_api import pool_add
    from chimera.api.connectors_api import set_key
    from chimera.cli.main import _set_env_var
    from chimera.integrations import openapi_store as store
    from tests.test_an_openapi_connector_is_get_only_untrusted_and_the_owners import _home

    home = _home(tmp_path)
    monkeypatch.setenv(store.derived_key_env("pets"), "")
    monkeypatch.setenv("CHIMERA_SANDBOX_IMAGE", "")
    monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "")
    monkeypatch.setenv("CHIMERA_KEY_VAULT", "false")
    for value in CASES:
        for name, write, key in (
            ("owner save", lambda v, e: patch_config({"CHIMERA_SANDBOX_IMAGE": v}, env_path=e),
             "CHIMERA_SANDBOX_IMAGE"),
            ("chimera init", lambda v, e: _set_env_var(e, "CHIMERA_SANDBOX_IMAGE", v),
             "CHIMERA_SANDBOX_IMAGE"),
        ):
            env = tmp_path / f"{name}.env"
            env.write_text(OWNER, encoding="utf-8")
            write(value, env)
            _assert_round_trip(env, key, value)
        if value and value == value.strip() and "," not in value:
            env = tmp_path / "pool.env"
            env.write_text(OWNER, encoding="utf-8")
            get_settings.cache_clear()
            monkeypatch.setenv("CHIMERA_OPENROUTER_KEYS", "")
            pool_add("openrouter", value, env_path=env)
            assert _read_all(env)["dotenv"]["CHIMERA_OPENROUTER_KEYS"] == value
            assert _read_all(env)["ours"]["CHIMERA_OPENROUTER_KEYS"] == value
            env = tmp_path / "connector.env"
            env.write_text(OWNER, encoding="utf-8")
            set_key(home, "pets", value, env)
            k = store.find(home, "pets").key_env
            assert _read_all(env)["dotenv"][k] == value
            assert _read_all(env)["dotenv"]["CHIMERA_REACH"] == "read_only"
    get_settings.cache_clear()


@pytest.mark.parametrize("value", REFUSED)
def test_what_no_spelling_makes_safe_is_refused_by_every_writer(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.cli.main import _set_env_var

    env = tmp_path / ".env"
    env.write_text(OWNER, encoding="utf-8")
    monkeypatch.setenv("CHIMERA_SANDBOX_IMAGE", "")
    for write in (
        lambda: write_env_value(env, "CHIMERA_SANDBOX_IMAGE", value),
        lambda: patch_config({"CHIMERA_SANDBOX_IMAGE": value}, env_path=env),
        lambda: _set_env_var(env, "CHIMERA_SANDBOX_IMAGE", value),
        lambda: encode_env_value("CHIMERA_SANDBOX_IMAGE", value),
    ):
        with pytest.raises(ValueError):
            write()
    assert env.read_text(encoding="utf-8") == OWNER
    get_settings.cache_clear()


def _bridge_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    for key in ("CHIMERA_SANDBOX_IMAGE", "CHIMERA_MEMORY_BACKEND", "CHIMERA_BROWSER_SITUATION"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    (tmp_path / ".env").write_text(OWNER, encoding="utf-8")
    return app


def _bridge_edit(client: TestClient, app: Any, body: dict[str, str]) -> Any:
    return client.post(
        "/api/bridge/call",
        json={"route": "settings.edit", "body": body},
        headers={"Authorization": f"Bearer {app.state.desktop_bridge.token}"},
    )


def test_the_reviews_proof_one_bridge_edit_no_longer_hides_the_owners_posture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's exact call: two bridge-writable keys, one opening a quote and one closing it.
    Afterwards every reader still sees the owner's posture lines, and the two values as sent."""
    app = _bridge_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = _bridge_edit(
            client,
            app,
            {"CHIMERA_SANDBOX_IMAGE": '"python:3.12-slim', "CHIMERA_MEMORY_BACKEND": 'x"'},
        )
    if r.json()["status"] == 200:
        env = tmp_path / ".env"
        _assert_round_trip(env, "CHIMERA_SANDBOX_IMAGE", '"python:3.12-slim')
        assert _read_all(env)["dotenv"]["CHIMERA_MEMORY_BACKEND"] == 'x"'
    else:  # refused whole is also safe; then nothing moved
        assert (tmp_path / ".env").read_text(encoding="utf-8") == OWNER
    for other, expected in OTHERS.items():
        assert _read_all(tmp_path / ".env")["model"][other] == expected
    get_settings.cache_clear()


def test_the_reviews_proof_an_interpolation_is_refused_through_the_bridge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRIPE_SK", "not-a-real-key-not-a-real-key")
    app = _bridge_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        r = _bridge_edit(client, app, {"CHIMERA_SANDBOX_IMAGE": "img:${STRIPE_SK}"})
        owner = client.patch("/api/config", json={"CHIMERA_SANDBOX_IMAGE": "img:${STRIPE_SK}"})
    assert r.json()["status"] == 400 and "${" in r.json()["data"]["detail"]
    assert owner.status_code == 400
    assert (tmp_path / ".env").read_text(encoding="utf-8") == OWNER
    get_settings.cache_clear()


@pytest.mark.parametrize("value", CASES)
def test_the_bridge_and_the_owner_round_trip_every_case(
    value: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _bridge_app(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    with TestClient(app) as client:
        through_bridge = _bridge_edit(client, app, {"CHIMERA_SANDBOX_IMAGE": value})
        assert through_bridge.json()["status"] == 200, through_bridge.text
        _assert_round_trip(env, "CHIMERA_SANDBOX_IMAGE", value)
        env.write_text(OWNER, encoding="utf-8")
        by_owner = client.patch("/api/config", json={"CHIMERA_SANDBOX_IMAGE": value})
        assert by_owner.status_code == 200, by_owner.text
        _assert_round_trip(env, "CHIMERA_SANDBOX_IMAGE", value)
    get_settings.cache_clear()
