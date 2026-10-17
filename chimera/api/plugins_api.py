"""``/api/plugins`` — the Settings › Plugins screen's door to :mod:`chimera.plugins` (plan B5).

Thin on purpose: every rule lives in the registry (``chimera/plugins/registry.py``), the marketplace
store and the MCP path, and each refusal they raise is answered here with its own sentence. What
this layer adds is the shape a card reads — the capability counts, the components, the held diff,
the servers the owner could add — and the split between the two kinds of caller:

* the owner's screen reaches every route;
* the desktop bridge (another agent driving the app) reaches only what the screens do without
  widening anything: list, validate, install PENDING, disable, check and apply an update (which only
  ever lands the plugin switched off), read a marketplace. Switching a plugin ON, adding its MCP
  servers, removing it, and adding or forgetting a marketplace are the owner's decisions, refused at
  every bridge tier (``bridge_routes.OWNER_DECISION_ROUTES``). The upload is absent from the bridge.

No install counts: Chimera is local and has no central telemetry, and a number nobody measured is not
shown (ESTUDO-01 §6.4). Nothing here touches the network except an install, an update or adding a
GitHub marketplace — each a click.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, UploadFile, params
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from chimera.config import Settings

#: Kinds whose text reaches the agent once the plugin is on (namespaced and tainted).
_TEXT_KINDS = frozenset({"skill", "command", "agent", "output_style"})
#: The hooks file of a suggestion is shown so the owner can copy it by hand; past this it is cut.
_SUGGESTION_TEXT_MAX = 16 * 1024


class PluginComponentOut(BaseModel):
    kind: str
    name: str
    path: str
    capability: str
    detail: list[str] = Field(default_factory=list)
    #: Kept on disk and never loaded: hooks, monitors, bin/, workflows, LSP servers, exec settings.
    inert: bool = False
    #: Its text reaches the agent when the plugin is on.
    reaches_agent: bool = False


class PluginServerOut(BaseModel):
    """One of the plugin's MCP servers: added only when the owner names it on enabling."""

    name: str
    configured_as: str
    added: bool
    capability: str
    detail: list[str] = Field(default_factory=list)


class PluginCapabilitiesOut(BaseModel):
    text: int = 0
    network: int = 0
    process: int = 0
    exec: int = 0


class PluginDiffComponentOut(BaseModel):
    kind: str
    name: str
    capability: str = ""


class PluginDiffOut(BaseModel):
    files_added: list[str] = Field(default_factory=list)
    files_removed: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    components_added: list[PluginDiffComponentOut] = Field(default_factory=list)
    components_removed: list[PluginDiffComponentOut] = Field(default_factory=list)
    components_changed: list[PluginDiffComponentOut] = Field(default_factory=list)
    governed: bool = False
    reasons: list[str] = Field(default_factory=list)


class PluginOut(BaseModel):
    name: str
    display_name: str = ""
    version: str = ""
    description: str = ""
    author: str = ""
    homepage: str = ""
    keywords: list[str] = Field(default_factory=list)
    source_kind: str = ""
    source: str = ""
    ref: str = ""
    tree_sha256: str = ""
    license: str = ""
    installed_at: str = ""
    updated_at: str = ""
    #: pending / active / inactive / held
    status: str = "pending"
    #: The deployment policy denies ``plugin:<name>`` right now: shown, never switched on.
    denied: bool = False
    marketplace: str = ""
    from_anthropic: bool = False
    capabilities: PluginCapabilitiesOut
    components: list[PluginComponentOut] = Field(default_factory=list)
    servers: list[PluginServerOut] = Field(default_factory=list)
    held_diff: PluginDiffOut | None = None
    mcp_added: list[str] = Field(default_factory=list)


class PluginsOut(BaseModel):
    plugins: list[PluginOut]
    #: No curated plugin catalogue ships with Chimera yet; the screen says so instead of inventing one.
    curated: bool = False


class PluginProblemOut(BaseModel):
    code: str
    severity: str
    where: str
    message: str


class PluginValidateIn(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class PluginValidationOut(BaseModel):
    kind: str
    ok: bool
    name: str = ""
    problems: list[PluginProblemOut] = Field(default_factory=list)
    capabilities: PluginCapabilitiesOut | None = None
    components: list[PluginComponentOut] = Field(default_factory=list)


class PluginInstallIn(BaseModel):
    """``source`` (a folder, ``github:owner/repo@<sha>[:folder]`` or ``https://…#sha256=<hex>``), or
    ``plugin`` + ``marketplace`` for an entry of a marketplace the owner added."""

    source: str = Field(default="", max_length=4096)
    plugin: str = Field(default="", max_length=128)
    marketplace: str = Field(default="", max_length=128)


class PluginEnableIn(BaseModel):
    #: The plugin's MCP servers the owner also adds, each by name. None is added otherwise.
    add_mcp: list[str] = Field(default_factory=list, max_length=50)


class PluginUpdateIn(BaseModel):
    #: A new pinned source; empty re-reads the recorded one.
    source: str = Field(default="", max_length=4096)


class PluginUpdateOut(BaseModel):
    name: str
    changed: bool
    #: The state the plugin is in (after applying) or would land in (a check).
    status: str
    applied: bool
    diff: PluginDiffOut | None = None


class PluginRemovedOut(BaseModel):
    removed: bool


class PluginSuggestionOut(BaseModel):
    kind: str
    name: str
    path: str
    capability: str
    detail: list[str] = Field(default_factory=list)
    state: str = "inert"
    #: The file it was read from, for the owner to copy into their own hooks by hand. Untrusted text.
    text: str = ""
    truncated: bool = False


class PluginSuggestionsOut(BaseModel):
    name: str
    suggestions: list[PluginSuggestionOut]


class PluginMarketplaceIn(BaseModel):
    """A local folder, or ``github:owner/repo@<40-hex sha>`` — a branch is not a pin."""

    source: str = Field(min_length=1, max_length=4096)


class PluginMarketplaceOut(BaseModel):
    name: str
    kind: str
    repo: str = ""
    sha: str = ""
    path: str = ""
    description: str = ""
    owner: str = ""
    added_at: str = ""
    plugins: int = 0


class PluginMarketplacesOut(BaseModel):
    marketplaces: list[PluginMarketplaceOut]


class PluginMarketplaceSourceOut(BaseModel):
    kind: str
    repo: str = ""
    sha: str = ""
    url: str = ""
    path: str = ""


class PluginMarketplaceEntryOut(BaseModel):
    name: str
    description: str = ""
    version: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    source: PluginMarketplaceSourceOut
    installable: bool
    #: Why it cannot be installed (unpinned, npm, a command…), in words. Empty when installable.
    refusal: str = ""
    #: The installed copy's state, or "" when not installed.
    installed: str = ""
    #: Known only for an entry this machine can read without a download (a local marketplace).
    capabilities: PluginCapabilitiesOut | None = None


class PluginMarketplaceEntriesOut(BaseModel):
    marketplace: str
    plugins: list[PluginMarketplaceEntryOut]


class PluginMarketplaceRemovedOut(BaseModel):
    removed: bool


# --- shaping ---------------------------------------------------------------------------------------


def _component(row: dict[str, Any]) -> dict[str, Any]:
    from chimera.plugins.registry import INERT_KINDS

    kind = str(row.get("kind", ""))
    capability = str(row.get("capability", ""))
    inert = kind in INERT_KINDS and (kind != "settings" or capability == "exec")
    return {
        "kind": kind,
        "name": str(row.get("name", "")),
        "path": str(row.get("path", "")),
        "capability": capability,
        "detail": [str(d) for d in row.get("detail") or []],
        "inert": inert,
        "reaches_agent": kind in _TEXT_KINDS,
    }


def _diff(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw:
        return None

    def rows(key: str) -> list[dict[str, Any]]:
        return [
            {"kind": str(r.get("kind", "")), "name": str(r.get("name", "")),
             "capability": str(r.get("capability") or (r.get("after") or {}).get("capability", ""))}
            for r in raw.get(key) or [] if isinstance(r, dict)
        ]

    return {
        "files_added": list(raw.get("files_added") or []),
        "files_removed": list(raw.get("files_removed") or []),
        "files_changed": list(raw.get("files_changed") or []),
        "components_added": rows("components_added"),
        "components_removed": rows("components_removed"),
        "components_changed": rows("components_changed"),
        "governed": bool(raw.get("governed")),
        "reasons": [str(r) for r in raw.get("reasons") or []],
    }


def _manifest_extras(home: Path, name: str) -> dict[str, Any]:
    """Display fields only the manifest carries, read from the installed tree (local, bounded)."""
    from chimera.plugins.manifest import read_plugin
    from chimera.plugins.registry import tree_of

    try:
        manifest = read_plugin(tree_of(home, name)).manifest
    except (OSError, ValueError):
        manifest = None
    if manifest is None:
        return {}
    return {
        "display_name": manifest.display_name or "",
        "author": manifest.author.name if manifest.author else "",
        "homepage": manifest.homepage or manifest.repository or "",
        "keywords": list(manifest.keywords),
    }


def describe(home: Path, record: Any) -> dict[str, Any]:
    """One installed plugin as a card reads it."""
    from chimera.plugins.mcp import plugin_servers

    servers = [
        {"name": str(s.get("name", "")), "configured_as": str(s.get("configured_as", "")),
         "added": bool(s.get("added")), "capability": str(s.get("capability", "")),
         "detail": [str(d) for d in s.get("detail") or []]}
        for s in plugin_servers(home, record.name)
    ]
    caps = record.capabilities or {}
    return {
        "name": record.name,
        "version": record.version,
        "description": record.description,
        "source_kind": record.source_kind,
        "source": record.source,
        "ref": record.ref,
        "tree_sha256": record.tree_sha256,
        "license": record.license,
        "installed_at": record.installed_at,
        "updated_at": record.updated_at,
        "status": record.status,
        "denied": record.denied,
        "marketplace": record.marketplace,
        "from_anthropic": record.from_anthropic,
        "capabilities": {k: int(caps.get(k, 0)) for k in ("text", "network", "process", "exec")},
        "components": [_component(r) for r in record.components],
        "servers": servers,
        "held_diff": _diff(record.held_diff) if record.status in ("held", "pending") else None,
        "mcp_added": list(record.mcp_added),
        **_manifest_extras(home, record.name),
    }


def _report_out(report: Any, kind: str) -> dict[str, Any]:
    from chimera.plugins.changes import component_rows

    problems = [
        {"code": p.code.value, "severity": p.severity.value, "where": p.where, "message": p.message}
        for p in report.problems
    ]
    if kind == "marketplace":
        return {"kind": kind, "ok": report.ok, "name": report.name or "", "problems": problems}
    caps = report.capabilities
    return {
        "kind": kind,
        "ok": report.ok,
        "name": report.manifest.name if report.manifest else "",
        "problems": problems,
        "capabilities": {"text": caps.text, "network": caps.network, "process": caps.process, "exec": caps.exec},
        "components": [_component(r) for r in component_rows(report.components)],
    }


def _entry_capabilities(market: dict[str, Any], entry: dict[str, Any]) -> dict[str, int] | None:
    """For a LOCAL marketplace's local entry, what the plugin would do — read from disk. A GitHub
    entry is not downloaded to answer a listing, so its capabilities are unknown until installed."""
    from chimera.plugins.manifest import read_plugin

    source = entry.get("source") or {}
    if market.get("kind") != "local" or source.get("kind") != "local" or not entry.get("installable"):
        return None
    folder = Path(str(market.get("path", ""))) / str(source.get("path") or "")
    try:
        report = read_plugin(folder, fallback_name=str(entry.get("name") or "") or None)
    except (OSError, ValueError):
        return None
    if report.manifest is None:
        return None
    caps = report.capabilities
    return {"text": caps.text, "network": caps.network, "process": caps.process, "exec": caps.exec}


def _http(exc: Exception) -> HTTPException:
    from chimera.plugins.registry import PluginDenied, PluginExists

    if isinstance(exc, PluginDenied):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, PluginExists):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


# --- routes ----------------------------------------------------------------------------------------


def register_plugins_api(app: FastAPI, guard: params.Depends, settings: Settings) -> None:
    """Mount the plugin routes. ``home`` is the launch photograph's, like every store's."""
    from chimera.plugins.registry import PluginError

    home = Path(settings.home)

    def _one(name: str) -> dict[str, Any]:
        from chimera.plugins.registry import get

        record = get(home, name)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no installed plugin named {name!r}")
        return describe(home, record)

    @app.get("/api/plugins", dependencies=[guard], response_model=PluginsOut)
    def list_plugins() -> dict[str, Any]:
        """Every installed plugin, denied ones included and marked."""
        from chimera.plugins.registry import installed

        return {"plugins": [describe(home, r) for r in installed(home)], "curated": False}

    @app.post("/api/plugins/validate", dependencies=[guard], response_model=PluginValidationOut)
    def validate_plugin(body: PluginValidateIn) -> dict[str, Any]:
        """Read a local plugin (or marketplace) folder and report what Claude Code would refuse.
        Copies nothing and runs nothing."""
        from chimera.plugins import read_marketplace, read_plugin
        from chimera.plugins.marketplace import MARKETPLACE

        folder = Path(body.path).expanduser()
        if not folder.is_absolute() or not folder.is_dir():
            raise HTTPException(status_code=400, detail="path must be an existing absolute folder")
        manifest = folder / ".claude-plugin" / "plugin.json"
        if (folder / MARKETPLACE).is_file() and not manifest.is_file():
            return _report_out(read_marketplace(folder), "marketplace")
        return _report_out(read_plugin(folder), "plugin")

    @app.post("/api/plugins/install", dependencies=[guard], response_model=PluginOut)
    async def install_plugin(body: PluginInstallIn) -> dict[str, Any]:
        """Install a plugin PENDING. Nothing reaches a prompt until the owner switches it on."""
        from chimera.plugins import marketplaces
        from chimera.plugins.registry import install

        def run() -> Any:
            if body.plugin and body.marketplace:
                spec = marketplaces.resolve(home, body.plugin, body.marketplace)
                return install(spec, home, marketplace=body.marketplace, fallback_name=body.plugin)
            if not body.source.strip():
                raise PluginError("name a source, or a plugin and the marketplace it is listed in")
            return install(body.source, home)

        try:
            record = await run_in_threadpool(run)
        except PluginError as exc:
            raise _http(exc) from exc
        return describe(home, record)

    @app.post("/api/plugins/{name}/enable", dependencies=[guard], response_model=PluginOut)
    def enable_plugin(name: str, body: PluginEnableIn) -> dict[str, Any]:
        """Switch a plugin on — the owner's act, never the bridge's. ``add_mcp`` names the servers
        the owner also adds, each through the MCP add path and its manifest pin."""
        from chimera.plugins.registry import enable

        try:
            record = enable(home, name, add_mcp=body.add_mcp)
        except PluginError as exc:
            raise _http(exc) from exc
        return describe(home, record)

    @app.post("/api/plugins/{name}/disable", dependencies=[guard], response_model=PluginOut)
    def disable_plugin(name: str) -> dict[str, Any]:
        """Switch a plugin off: its components disappear and the servers it added are removed."""
        from chimera.plugins.registry import disable

        try:
            record = disable(home, name)
        except PluginError as exc:
            raise _http(exc) from exc
        return describe(home, record)

    @app.get("/api/plugins/{name}/update", dependencies=[guard], response_model=PluginUpdateOut)
    async def check_plugin_update(name: str, source: str = "") -> dict[str, Any]:
        """Whether the source changed, and the diff, changing nothing on disk."""
        from chimera.plugins.registry import check_update

        _one(name)
        try:
            result = await run_in_threadpool(check_update, home, name, source or None)
        except PluginError as exc:
            raise _http(exc) from exc
        return {"name": name, "changed": result.changed, "status": result.status, "applied": False,
                "diff": _diff(result.diff)}

    @app.post("/api/plugins/{name}/update", dependencies=[guard], response_model=PluginUpdateOut)
    async def apply_plugin_update(name: str, body: PluginUpdateIn) -> dict[str, Any]:
        """Apply an update. Any change leaves the plugin held or pending — switched off — with the
        diff stored on it, until the owner switches it on again."""
        from chimera.plugins.registry import update

        _one(name)
        try:
            result = await run_in_threadpool(update, home, name, body.source or None)
        except PluginError as exc:
            raise _http(exc) from exc
        return {"name": name, "changed": result.changed, "status": result.status, "applied": True,
                "diff": _diff(result.diff)}

    @app.delete("/api/plugins/{name}", dependencies=[guard], response_model=PluginRemovedOut)
    def remove_plugin(name: str) -> dict[str, Any]:
        """Delete an installed plugin and the MCP servers it added."""
        from chimera.plugins.registry import remove

        if not remove(home, name):
            raise HTTPException(status_code=404, detail=f"no installed plugin named {name!r}")
        return {"removed": True}

    @app.get("/api/plugins/{name}/suggestions", dependencies=[guard], response_model=PluginSuggestionsOut)
    def plugin_suggestions(name: str) -> dict[str, Any]:
        """Hooks, monitors, executables, workflows and exec settings: listed, never run. The file
        each came from is included so the owner can copy a hook into their own hooks by hand."""
        from chimera.plugins.registry import get, inert_suggestions, tree_of
        from chimera.plugins.scan import read_text, scan

        record = get(home, name)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no installed plugin named {name!r}")
        index = scan(tree_of(home, name))
        out = []
        for row in inert_suggestions(record):
            rel = str(row.get("path", "")).partition("#")[0]
            text = (read_text(index, rel, []) or "") if index.is_file(rel) else ""
            out.append({
                **_component(row), "state": "inert",
                "text": text[:_SUGGESTION_TEXT_MAX], "truncated": len(text) > _SUGGESTION_TEXT_MAX,
            })
        return {"name": name, "suggestions": out}

    # --- marketplaces ---

    @app.get("/api/plugins/marketplaces", dependencies=[guard], response_model=PluginMarketplacesOut)
    def list_marketplaces() -> dict[str, Any]:
        from chimera.plugins import marketplaces

        return {"marketplaces": [
            {"name": str(m.get("name", "")), "kind": str(m.get("kind", "")), "repo": str(m.get("repo", "")),
             "sha": str(m.get("sha", "")), "path": str(m.get("path", "")),
             "description": str(m.get("description", "")), "owner": str(m.get("owner", "")),
             "added_at": str(m.get("added_at", "")), "plugins": len(m.get("plugins") or [])}
            for m in marketplaces.load(home)
        ]}

    @app.post("/api/plugins/marketplaces", dependencies=[guard], response_model=PluginMarketplaceOut)
    async def add_marketplace(body: PluginMarketplaceIn) -> dict[str, Any]:
        """Remember a marketplace — the owner's act. Its ``marketplace.json`` is read now."""
        from chimera.plugins import marketplaces

        try:
            m = await run_in_threadpool(marketplaces.add, home, body.source)
        except PluginError as exc:
            raise _http(exc) from exc
        return {"name": m["name"], "kind": m["kind"], "repo": m.get("repo", ""), "sha": m.get("sha", ""),
                "path": m.get("path", ""), "description": m.get("description", ""),
                "owner": m.get("owner", ""), "added_at": m.get("added_at", ""),
                "plugins": len(m.get("plugins") or [])}

    @app.delete("/api/plugins/marketplaces/{name}", dependencies=[guard],
                response_model=PluginMarketplaceRemovedOut)
    def remove_marketplace(name: str) -> dict[str, Any]:
        from chimera.plugins import marketplaces

        if not marketplaces.remove(home, name):
            raise HTTPException(status_code=404, detail=f"no marketplace named {name!r}")
        return {"removed": True}

    @app.get("/api/plugins/marketplaces/{name}/plugins", dependencies=[guard],
             response_model=PluginMarketplaceEntriesOut)
    def marketplace_plugins(name: str) -> dict[str, Any]:
        """What an added marketplace lists, as it was read when added — no download. An unpinned or
        unsupported entry is listed with the reason and is not installable."""
        from chimera.plugins import marketplaces
        from chimera.plugins.registry import installed

        market = next((m for m in marketplaces.load(home) if m.get("name") == name), None)
        if market is None:
            raise HTTPException(status_code=404, detail=f"no marketplace named {name!r}")
        here = {r.name: r.status for r in installed(home)}
        entries = []
        for entry in market.get("plugins") or []:
            source = entry.get("source") or {}
            entries.append({
                "name": str(entry.get("name", "")),
                "description": str(entry.get("description", "")),
                "version": str(entry.get("version", "")),
                "category": str(entry.get("category", "")),
                "tags": [str(t) for t in entry.get("tags") or []],
                "source": {k: str(source.get(k) or "") for k in ("kind", "repo", "sha", "url", "path")},
                "installable": bool(entry.get("installable")),
                "refusal": str(entry.get("refusal") or ""),
                "installed": here.get(str(entry.get("name", "")), ""),
                "capabilities": _entry_capabilities(market, entry),
            })
        return {"marketplace": name, "plugins": entries}

    _register_upload(app, guard, settings, home)


def _register_upload(app: FastAPI, guard: params.Depends, settings: Settings, home: Path) -> None:
    """``POST /api/plugins/upload`` — on its own router for the route class that refuses a cross-site
    or oversized body before it is parsed, the same one the skill upload uses."""
    from fastapi import File, Form

    from chimera.api.features import _upload_route
    from chimera.plugins.scan import MAX_TOTAL_BYTES

    def named_origins() -> list[str]:
        raw = str(getattr(settings, "allowed_origins", "") or "")
        return [o.strip() for o in raw.split(",") if o.strip()]

    router = APIRouter(route_class=_upload_route(named_origins, MAX_TOTAL_BYTES + 1024 * 1024))
    files_field = File(..., description="One .zip/.tar.gz, or every file of a plugin folder.")
    paths_field = Form(default=[], description="For a folder: each file's path, in the order of `files`.")

    @router.post("/api/plugins/upload", dependencies=[guard], response_model=PluginOut)
    async def upload_plugin(
        files: list[UploadFile] = files_field,
        paths: list[str] = paths_field,
        replace: bool = False,
    ) -> dict[str, Any]:
        """A plugin from the owner's disk. Lands pending; with ``replace`` an installed one is
        updated and lands held or pending with the diff. 409 when the name is taken."""
        from chimera.plugins.registry import PluginError, get
        from chimera.plugins.upload import import_upload

        named = len(paths) == len(files)
        received: list[tuple[str, bytes]] = []
        total = 0
        for index, upload in enumerate(files):
            data = await upload.read(MAX_TOTAL_BYTES + 1)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail="the upload is larger than the plugin limit")
            received.append(((paths[index] if named else upload.filename) or "upload.zip", data))
        label = received[0][0].split("/", 1)[0] if received and "/" in received[0][0] else ""
        try:
            result = await run_in_threadpool(import_upload, received, home, replace=replace, label=label)
        except PluginError as exc:
            raise _http(exc) from exc
        record = get(home, getattr(result, "name", ""))
        if record is None:
            raise HTTPException(status_code=500, detail="the plugin was not found after the upload")
        return describe(home, record)

    app.include_router(router)


__all__ = ["describe", "register_plugins_api"]
