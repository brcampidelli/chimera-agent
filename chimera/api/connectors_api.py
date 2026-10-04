"""``/api/connectors`` — the OpenAPI connectors on the Connections screen (study 29, P7.5).

The store and the tool rules live in :mod:`chimera.integrations.openapi_store`; this is the screen's
door to them, and two things about the door matter:

* **It is the owner's door only.** None of these routes is in the desktop bridge's table
  (`bridge_routes.ROUTES`), so a client holding the bridge token — another agent driving the app —
  cannot add a connector, switch one on, allow it to change data, or set its key. Each of those
  widens what the agent can reach or where the owner's data goes.
* **A key goes in and never comes out.** ``PUT …/key`` writes ``.env``; every read reports
  ``key_set`` and at most the last four characters, the same rule as the settings screen.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params

from chimera.api.schemas import (
    ConnectorAddIn,
    ConnectorKeyIn,
    ConnectorOut,
    ConnectorPatchIn,
    ConnectorsOut,
    DeletedOut,
)
from chimera.config import Settings
from chimera.integrations import openapi_store as store


def describe(home: Path, cfg: store.ConnectorConfig, env_path: Path | None = None) -> dict[str, Any]:
    """One connector as the screen shows it — the key as ``{set, hint}``, never its value."""
    from chimera.api.config_api import _hint

    problem = ""
    ops: list[dict[str, Any]] = []
    try:
        selected = set(cfg.operations)
        ops = [
            {
                "id": op.id, "tool": op.tool, "method": op.method, "path": op.path,
                "summary": op.summary, "selected": op.id in selected,
            }
            for op in store.operations(store.load_snapshot(home, cfg.name), cfg.name)
        ]
    except (OSError, ValueError):
        problem = "the pinned copy of the spec is missing or unreadable — remove and add it again"
    key = store.read_key(cfg.key_env, env_path) if cfg.key_env in store.allowed_key_envs(cfg.name) else ""
    return {
        "name": cfg.name,
        "source": cfg.source,
        "base_url": cfg.base_url,
        "enabled": cfg.enabled,
        "allow_writes": cfg.allow_writes,
        "unattended": cfg.unattended,
        "key_env": cfg.key_env,
        "key_envs": store.allowed_key_envs(cfg.name),
        "key_in": cfg.key_in,
        "key_name": cfg.key_name,
        "key_prefix": cfg.key_prefix,
        "key_set": bool(key),
        "key_hint": _hint(key),
        "added_at": cfg.added_at,
        "operations": ops,
        "problem": problem,
    }


def list_connectors(home: Path, env_path: Path | None = None) -> dict[str, Any]:
    return {
        "connectors": [describe(home, cfg, env_path) for cfg in store.load(home)],
        "store": str(Path(home) / store.STORE_FILE),
    }


def set_key(home: Path, name: str, value: str, env_path: Path | None = None) -> dict[str, Any]:
    """Write the connector's key to ``.env`` (and the live environment), under the name it reads."""
    from chimera.api.config_api import _write_env_var
    from chimera.config import get_settings

    cfg = store.find(home, name)
    if cfg.key_env not in store.allowed_key_envs(name):
        raise store.ConnectorError("choose which variable this connector reads its key from first")
    candidate = (value or "").strip()
    if not candidate:
        raise store.ConnectorError("the key may not be empty")
    if any(c in candidate for c in "\r\n"):
        raise store.ConnectorError("the key may not contain a newline")
    from chimera.api.key_vault import check_env_value

    try:
        # Every line-breaking character, not only \r and \n: the .env writer would otherwise be
        # handed a value it refuses, after the checks above said yes.
        check_env_value(cfg.key_env, candidate)
    except ValueError as exc:
        raise store.ConnectorError(str(exc)) from None
    if candidate.startswith("…") or set(candidate) <= {"*", "•", "·"}:
        raise store.ConnectorError("that looks like a masked hint, not a key")
    _write_env_var(env_path or Path(".env"), cfg.key_env, candidate)
    os.environ[cfg.key_env] = candidate
    get_settings.cache_clear()
    return describe(home, cfg, env_path)


def register_connectors_api(
    app: FastAPI,
    guard: params.Depends,
    settings: Settings,
    *,
    env_path: Callable[[], Path | None] = lambda: None,
) -> None:
    """Mount the connector routes. ``home`` is the launch photograph's, like every store's."""
    home = Path(settings.home)

    def _fail(exc: store.ConnectorError) -> HTTPException:
        missing = str(exc).startswith("no connector named")
        return HTTPException(status_code=404 if missing else 400, detail=str(exc))

    @app.get("/api/connectors", dependencies=[guard], response_model=ConnectorsOut)
    def connectors_list_endpoint() -> dict[str, Any]:
        """The configured connectors. File reads only — nothing is fetched or connected."""
        return list_connectors(home, env_path())

    @app.post("/api/connectors", dependencies=[guard], response_model=ConnectorOut)
    def connectors_add_endpoint(body: ConnectorAddIn) -> dict[str, Any]:
        """Fetch the spec once (SSRF-guarded), pin it, and record the connector switched OFF."""
        try:
            cfg = store.add(home, body.name, body.source, base_url=body.base_url)
        except store.ConnectorError as exc:
            raise _fail(exc) from None
        return describe(home, cfg, env_path())

    @app.patch("/api/connectors/{name}", dependencies=[guard], response_model=ConnectorOut)
    def connectors_patch_endpoint(name: str, body: ConnectorPatchIn) -> dict[str, Any]:
        """Switch it on or off, allow changes, send it to the unattended surfaces, pick operations,
        or say how the key is sent."""
        try:
            cfg = store.update(home, name, body.model_dump(exclude_none=True))
        except store.ConnectorError as exc:
            raise _fail(exc) from None
        return describe(home, cfg, env_path())

    @app.put("/api/connectors/{name}/key", dependencies=[guard], response_model=ConnectorOut)
    def connectors_key_endpoint(name: str, body: ConnectorKeyIn) -> dict[str, Any]:
        """Store the key in ``.env``. Write-only: the answer carries at most its last four characters."""
        try:
            return set_key(home, name, body.value, env_path())
        except store.ConnectorError as exc:
            raise _fail(exc) from None

    @app.delete("/api/connectors/{name}", dependencies=[guard], response_model=DeletedOut)
    def connectors_remove_endpoint(name: str) -> dict[str, bool]:
        """Forget the connector and its pinned spec. Its key stays in ``.env`` — a file the owner edits."""
        return {"deleted": store.remove(home, name)}
