"""The OpenAPI connectors the owner added, and the tools they become (study 29, P7.5).

`chimera/integrations/openapi.py` could turn a spec into tools since early on, and nothing gave the
agent one: its only caller outside the tests was `chimera schema-bench`, which counts schema tokens.
This module is the missing half — a store in ``<home>/connectors.json`` the Connections screen
writes and every assembly reads, and a tool shape that is safe to hand an agent.

What "safe" means here, rule by rule, because each one is a decision and not a default:

* **Nothing loads until the owner says so.** A connector is added switched OFF, with only its GET
  operations selected. A spec is somebody else's text (its descriptions reach the model as tool
  descriptions), so it lands pending, like an MCP server or a skill bundle does.
* **The spec is pinned.** It is fetched once, when the owner adds it, and kept as a snapshot under
  ``<home>/connectors/``. Loading never touches the network: a spec re-read at every boot would let
  its publisher add operations the owner never saw, and an unreachable host would decide whether the
  agent has its tools today.
* **GET only, unless the owner allows more — and then every call still asks.** An operation that
  is not GET cannot be selected while the connector's "allow changes" switch is off, and with it on,
  each call is a question on the owner's screen (``ask_outside``, the approver the Code turn hands
  the file tools). Where nobody can be asked — a headless run, the VPS — it is a refusal.
* **Every URL passes the SSRF guard**, on every redirect hop, and the request may not leave the
  origin the owner configured, nor climb out of its path — not by a redirect either: a GET follows
  one only inside that origin and base path, a call that can change data follows none (the 3xx is
  its answer), so the request the owner approved is the only one sent. A path parameter is
  percent-encoded, so a model cannot spell ``../`` or ``@evil.test`` into it, and a bare ``.`` or
  ``..`` segment — which encoding leaves alone — is refused.
* **The key is never shown and never echoed.** It lives in ``.env`` under a name this module
  derives (or one of the three reserved tool keys) — always a name the redaction and the shell's
  child environment treat as a secret; the store holds only the variable's NAME. It is
  only ever sent to the configured origin, kept out of the URL a result reports, and masked in the
  body before the body is cut to size.
* **Responses are untrusted.** The tool carries ``untrusted_output``, so wherever a taint ledger
  wraps the registry it fences what the tool returns and marks the run, as it does for a web page
  or an MCP server. That is the app's Code screen always; the app's chat only while
  ``CHIMERA_GUARD_CHAT`` is on (the default — ``guard_chat_registry`` is the chat's ledger and is
  skipped when it is off); ``/v1/chat/completions`` never, because it has no ledger at all; and
  the governed surfaces only when governance is not ``off`` — ``governed_profile`` returns before
  building its ledger on ``off``, the shipped default. Where no ledger wraps the registry, the
  marker is a label and nothing reads it: the connector's answer reaches the model unfenced.
* **The app first; anywhere else only when the owner sends it there.** The Code screen and the
  app's chat load every switched-on connector. The unattended surfaces — the bots, the cron, the
  board's lanes, the MCP and A2A servers — load only a connector whose ``unattended`` switch is
  on, and a bot surface not even then while any configured bot answers anyone (no allowlist):
  a GET runs with the owner's key, so an open bot would hand a stranger the owner's private API.
  For the same reason a guest's turn in a shared conversation loads none, whatever the switches
  say (``assemble_registry(guest=True)``): the share link is the owner's call for the
  conversation, not for their private APIs.
* **The deployment's lists still apply.** The tools are poured into the registry BEFORE the
  allowlist / denylist filter runs, so ``CHIMERA_TOOL_DENYLIST`` reaches them by name.

What this deliberately does not do: re-fetch or diff a spec (remove and add again to update it),
OAuth flows, multipart bodies, or more than one credential per connector.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus, unquote, urljoin, urlsplit

import yaml

from chimera.integrations.openapi import RestApiTool, _retry_delay, _sanitize, tools_from_openapi
from chimera.telemetry import get_logger
from chimera.tools.base import Tool, refusal

_log = get_logger("integrations.openapi_store")

STORE_FILE = "connectors.json"
SPEC_DIR = "connectors"

#: A connector's name becomes part of every tool it registers (``api_<name>_<op>``) and of its key's
#: variable name, so it is held to the characters both of those allow — and short, because a tool
#: name is capped at 64 characters by the providers and the operation id needs the rest.
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
#: The keys a connector may read. Its own derived variable, or one of the three slots the settings
#: screen has listed for years as "reserved; no built-in tool uses this key". Never an arbitrary
#: name: a connector that could name ``OPENAI_API_KEY`` would send the owner's model key to whatever
#: host the spec points at.
RESERVED_KEY_ENVS = ("BRAVE_API_KEY", "SERPAPI_API_KEY", "STABILITY_API_KEY")
_HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_QUERY_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
#: Header names a key may not be sent as: these change what the request IS, not who sends it.
_FORBIDDEN_HEADERS = frozenset({"host", "cookie", "content-length", "content-type", "transfer-encoding"})

_MAX_SPEC_BYTES = 5 * 1024 * 1024
_MAX_BODY_BYTES = 10 * 1024 * 1024
_MAX_BODY_CHARS = 20_000
#: How much of a request body the owner's approval card shows — enough to read what is being sent,
#: bounded so a large upload cannot push the question itself off the card.
_MAX_ASK_BODY_CHARS = 2_000
_MAX_REDIRECTS = 5
_MAX_TOOL_NAME = 64
_TIMEOUT = 30.0
_RETRY_STATUS = {429, 500, 502, 503, 504}
_MASK = "[redacted]"


#: Serialises every read-modify-write of ``connectors.json``. FastAPI runs these sync routes on a
#: thread pool, and the screen sends its edits as separate requests (the on/off switch and the key
#: style are different mutations), so two of them could each load the file, change one field, and
#: save — the second save writing back the field the first had just changed. The owner switches a
#: connector off and a concurrent save switches it on again. Re-entrant because ``add`` and
#: ``update`` call ``load``/``save``, which take it too.
_LOCK = threading.RLock()


class ConnectorError(ValueError):
    """A request the store refuses, in words fit to show the owner (never a remote body)."""


@dataclass
class ConnectorConfig:
    """One connector as ``connectors.json`` holds it. No secret is ever in here — only a NAME."""

    name: str
    source: str
    base_url: str
    enabled: bool = False
    allow_writes: bool = False
    operations: list[str] = field(default_factory=list)
    key_env: str = ""
    key_in: str = "header"
    key_name: str = "Authorization"
    key_prefix: str = "Bearer "
    added_at: str = ""
    #: Off: only the app (the Code screen, its chat) loads this connector. On: the unattended
    #: surfaces too — see :func:`unattended_surface_may_load` for the one place that still says no.
    unattended: bool = False


@dataclass(frozen=True)
class Operation:
    """One operation of a pinned spec, as the screen lists it."""

    id: str
    tool: str
    method: str
    path: str
    summary: str


# ------------------------------------------------------------------ names


def derived_key_env(name: str) -> str:
    """The ``.env`` variable a connector's own key lives in: ``CHIMERA_CONNECTOR_<NAME>_API_KEY``.

    ``_API_KEY`` and not ``_KEY``, because the NAME is what the rest of the codebase reads to decide
    a value is a secret (`chimera.core.redact._SECRET_MARKERS`): the shell's child environment drops
    a variable whose name carries a marker, and `redact` masks its value in transcripts and logs.
    ``_KEY`` alone carries none, so a key `PUT …/key` had exported to the live environment was
    handed to every `run_shell` child — one `env` away from the model's context — and left unmasked
    in the logs. Every name :func:`allowed_key_envs` returns must carry a marker; a test holds that.
    """
    return f"CHIMERA_CONNECTOR_{name.upper()}_API_KEY"


def allowed_key_envs(name: str) -> list[str]:
    return [derived_key_env(name), *RESERVED_KEY_ENVS]


#: Every connector tool starts with this, and no tool Chimera ships does (a test holds that).
TOOL_PREFIX = "api_"


def tool_name(connector: str, operation_id: str) -> str:
    """``api_<connector>_<operationId>``: namespaced and bounded.

    The connector name alone was not a namespace. ``mount`` skips a name already in the registry,
    but several surfaces register tools AFTER it — the bots' ``send_message``, the Code screen's
    ``explore_repository``, a turn's ``extra_tools`` — and a connector named ``send`` whose spec
    has an operation ``message`` became ``send_message``: the bot's own registration then raised
    ``DuplicateToolError`` and every conversation failed. The operation id is a third party's text,
    so the fix cannot be a list of names to avoid; it is a prefix no built-in uses.
    """
    return f"{TOOL_PREFIX}{connector}_{_sanitize(operation_id)}"[:_MAX_TOOL_NAME]


# ------------------------------------------------------------------ the file


def _store(home: Path) -> Path:
    return Path(home) / STORE_FILE


def _spec_path(home: Path, name: str) -> Path:
    return Path(home) / SPEC_DIR / f"{name}.json"


def load(home: Path) -> list[ConnectorConfig]:
    """The configured connectors. A missing file is none; a broken one is none, loudly.

    Fail-closed on purpose: a file that does not parse is not evidence of what the owner switched
    on, so nothing in it loads.
    """
    path = _store(home)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("connectors: %s is unreadable (%s) — no connector loads", path, type(exc).__name__)
        return []
    entries = raw.get("connectors") if isinstance(raw, dict) else None
    out: list[ConnectorConfig] = []
    known = set(ConnectorConfig.__dataclass_fields__)
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            cfg = ConnectorConfig(**{k: v for k, v in entry.items() if k in known})
        except TypeError:
            continue
        if not _NAME.match(str(cfg.name)):
            _log.warning("connectors: skipping an entry with an invalid name")
            continue
        cfg.operations = [str(op) for op in cfg.operations] if isinstance(cfg.operations, list) else []
        out.append(cfg)
    return out


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def save(home: Path, configs: list[ConnectorConfig]) -> None:
    body = {"connectors": [asdict(c) for c in configs]}
    _atomic_write(_store(home), json.dumps(body, indent=2, ensure_ascii=False) + "\n")


def find(home: Path, name: str) -> ConnectorConfig:
    for cfg in load(home):
        if cfg.name == name:
            return cfg
    raise ConnectorError(f"no connector named {name!r}")


def load_snapshot(home: Path, name: str) -> dict[str, Any]:
    data = json.loads(_spec_path(home, name).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConnectorError(f"the pinned spec of {name!r} is not an object")
    return data


# ------------------------------------------------------------------ the spec


class _NoAliasLoader(yaml.SafeLoader):
    """``SafeLoader`` that refuses YAML aliases (``*name``).

    ``safe_load`` keeps an alias as a shared reference, which costs nothing in memory — until the
    spec is pinned as JSON, which writes every reference out in full. Seven levels of ten aliases
    is ~700 bytes of YAML and a 110 MB snapshot; one level more is ~1.1 GB and the sidecar falls
    over inside ``POST /api/connectors``. The 5 MB cap measured only the bytes downloaded, and the
    spec's author — a third party — chooses the shape. An OpenAPI document has no need of aliases
    (``$ref`` is its own reuse), so refusing them costs a real spec nothing, and refusing is the
    only bound that holds BEFORE the expansion: no alias count limits ``10**depth``.
    """

    def compose_node(self, parent: Any, index: Any) -> Any:
        # The PyYAML stubs leave the parser's event API unannotated; it is the documented hook.
        if self.check_event(yaml.AliasEvent):  # type: ignore[no-untyped-call]
            raise ConnectorError("the spec uses YAML aliases (*name), which a connector refuses")
        return super().compose_node(parent, index)


def _parse_spec(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        try:
            data = yaml.load(text, Loader=_NoAliasLoader)  # noqa: S506 — a SafeLoader subclass
        except yaml.YAMLError as exc:
            raise ConnectorError("the spec is neither JSON nor YAML") from exc
    if not isinstance(data, dict) or not isinstance(data.get("paths"), dict):
        raise ConnectorError("that is not an OpenAPI document (no 'paths' object)")
    return data


def fetch_spec(source: str) -> dict[str, Any]:
    """Read a spec from an http(s) URL — through the SSRF guard, on every hop — or a local file."""
    if source.startswith(("http://", "https://")):
        return _parse_spec(_guarded_get_text(source))
    path = Path(source).expanduser()
    if not path.is_file():
        raise ConnectorError(f"no file at {source!r}")
    if path.stat().st_size > _MAX_SPEC_BYTES:
        raise ConnectorError("the spec is larger than 5 MB")
    return _parse_spec(path.read_text(encoding="utf-8"))


def _guarded_get_text(url: str) -> str:
    import httpx

    from chimera.scrape.ssrf import check_url

    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                check_url(url)
                with client.stream("GET", url) as response:
                    if response.is_redirect and response.headers.get("location"):
                        url = str(httpx.URL(url).join(response.headers["location"]))
                        continue
                    if response.status_code >= 400:
                        raise ConnectorError(f"the spec URL answered {response.status_code}")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw += chunk
                        if len(raw) > _MAX_SPEC_BYTES:
                            raise ConnectorError("the spec is larger than 5 MB")
                    return bytes(raw).decode(response.encoding or "utf-8", errors="replace")
    except ValueError as exc:  # the SSRF guard, or our own ConnectorError
        raise ConnectorError(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise ConnectorError(f"could not fetch the spec ({type(exc).__name__})") from exc
    raise ConnectorError("the spec URL redirected too many times")


def spec_base_url(spec: dict[str, Any], source: str) -> str:
    """Where the operations live: OpenAPI 3's first server, Swagger 2's host, or the spec's origin."""
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        url = str(servers[0].get("url") or "")
        if url and source.startswith(("http://", "https://")):
            url = urljoin(source, url)  # a relative server ("/api/v3") is relative to the spec
        if url:
            return url.rstrip("/")
    host = spec.get("host")
    if isinstance(host, str) and host:
        schemes = spec.get("schemes") if isinstance(spec.get("schemes"), list) else []
        scheme = "https" if not schemes or "https" in schemes else str(schemes[0])
        return f"{scheme}://{host}{spec.get('basePath') or ''}".rstrip("/")
    if source.startswith(("http://", "https://")):
        parts = urlsplit(source)
        return f"{parts.scheme}://{parts.netloc}"
    return ""


def check_base_url(url: str) -> str:
    """An origin the owner can be held to: http(s), a host, no credentials, no query, no fragment.

    Not resolved here — the guard runs on every request, where it counts.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConnectorError("the base URL must be an http(s) URL with a host")
    if parts.username or parts.password:
        raise ConnectorError("the base URL may not carry a user name or password")
    if parts.query or parts.fragment:
        raise ConnectorError("the base URL may not carry a query or a fragment")
    return url.strip().rstrip("/")


def operations(spec: dict[str, Any], connector: str) -> list[Operation]:
    """Every operation in ``spec``, by the id :func:`tools_from_openapi` names its tool with."""
    out: list[Operation] = []
    seen: set[str] = set()
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"} or not isinstance(op, dict):
                continue
            op_id = str(op.get("operationId") or f"{method}_{_sanitize(str(path))}")
            if op_id in seen:
                continue  # two operations with one id would be one tool; the first wins, as below
            seen.add(op_id)
            summary = str(op.get("summary") or op.get("description") or "")[:200]
            out.append(Operation(op_id, tool_name(connector, op_id), method.upper(), str(path), summary))
    return out


# ------------------------------------------------------------------ edits


def add(home: Path, name: str, source: str, *, base_url: str | None = None) -> ConnectorConfig:
    """Fetch and pin a spec, and record the connector switched OFF with only its GETs selected."""
    if not _NAME.match(name or ""):
        raise ConnectorError(
            "a connector name is 1-24 characters: a lowercase letter, then letters, digits or '_'"
        )
    source = (source or "").strip()
    if not source:
        raise ConnectorError("give the spec's URL or a file path")
    if any(c.name == name for c in load(home)):
        raise ConnectorError(f"a connector named {name!r} already exists — remove it first")
    # Fetched OUTSIDE the lock: a slow spec host must not hold every other edit of the file.
    spec = fetch_spec(source)
    resolved = check_base_url(base_url or spec_base_url(spec, source) or "")
    ops = operations(spec, name)
    if not ops:
        raise ConnectorError("the spec declares no operations")
    # Measured AFTER serialising, as well as when downloaded: the snapshot is what every turn and
    # every list call re-reads, so it is the size that has to stay bounded.
    pinned = json.dumps(spec, ensure_ascii=False)
    if len(pinned.encode("utf-8")) > _MAX_SPEC_BYTES:
        raise ConnectorError("the spec is larger than 5 MB once written out")
    cfg = ConnectorConfig(
        name=name,
        source=source,
        base_url=resolved,
        operations=[op.id for op in ops if op.method == "GET"],
        key_env=derived_key_env(name),
        added_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    with _LOCK:
        configs = load(home)  # again, under the lock: another add may have landed meanwhile
        if any(c.name == name for c in configs):
            raise ConnectorError(f"a connector named {name!r} already exists — remove it first")
        _atomic_write(_spec_path(home, name), pinned)
        save(home, [*configs, cfg])
    return cfg


def update(home: Path, name: str, changes: dict[str, Any]) -> ConnectorConfig:
    """Apply the screen's edits. Only narrows by itself: a non-GET operation needs ``allow_writes``."""
    with _LOCK:
        return _update(home, name, changes)


def _pinned_operations(home: Path, name: str) -> dict[str, Operation] | None:
    """The pinned spec's operations by id, or ``None`` when the snapshot is missing or unreadable."""
    try:
        return {op.id: op for op in operations(load_snapshot(home, name), name)}
    except (OSError, ValueError):
        return None


def _update(home: Path, name: str, changes: dict[str, Any]) -> ConnectorConfig:
    configs = load(home)
    cfg = next((c for c in configs if c.name == name), None)
    if cfg is None:
        raise ConnectorError(f"no connector named {name!r}")
    if "enabled" in changes and changes["enabled"] is not None:
        cfg.enabled = bool(changes["enabled"])
    if "allow_writes" in changes and changes["allow_writes"] is not None:
        cfg.allow_writes = bool(changes["allow_writes"])
    if "unattended" in changes and changes["unattended"] is not None:
        cfg.unattended = bool(changes["unattended"])
    if changes.get("base_url") is not None:
        cfg.base_url = check_base_url(str(changes["base_url"]))
    if changes.get("key_env") is not None:
        env = str(changes["key_env"])
        if env and env not in allowed_key_envs(name):
            raise ConnectorError(f"a connector may read only {', '.join(allowed_key_envs(name))}")
        cfg.key_env = env
    if changes.get("key_in") is not None:
        if changes["key_in"] not in ("header", "query"):
            raise ConnectorError("the key goes in a 'header' or in the 'query'")
        cfg.key_in = str(changes["key_in"])
    if changes.get("key_name") is not None:
        cfg.key_name = str(changes["key_name"]).strip()
    if changes.get("key_prefix") is not None:
        prefix = str(changes["key_prefix"])
        if any(c in prefix for c in "\r\n"):
            raise ConnectorError("the key prefix may not contain a newline")
        cfg.key_prefix = prefix
    _check_key_name(cfg)
    pinned = _pinned_operations(home, name)
    if pinned is None:
        # The snapshot is gone or broken. That raised FileNotFoundError / JSONDecodeError here —
        # not a ConnectorError, so the route answered 500 — for EVERY edit, switching the connector
        # off included, which left the owner unable to turn off a connector they could see was
        # broken. Narrowing edits go through; the two that need the spec are refused in words.
        if changes.get("operations") is not None or bool(changes.get("enabled")):
            raise ConnectorError(
                "the pinned copy of the spec is missing or unreadable — remove and add it again"
            )
        save(home, configs)
        return cfg
    by_id = pinned
    if changes.get("operations") is not None:
        wanted = [str(op) for op in changes["operations"]]
        unknown = [op for op in wanted if op not in by_id]
        if unknown:
            raise ConnectorError(f"not in the pinned spec: {', '.join(unknown[:5])}")
        cfg.operations = wanted
    if not cfg.allow_writes:
        writes = [op for op in cfg.operations if by_id.get(op) and by_id[op].method != "GET"]
        if writes and changes.get("operations") is not None:
            raise ConnectorError(
                "an operation that is not GET needs 'allow changes' switched on first: "
                + ", ".join(writes[:5])
            )
        # Switching writes off takes the selected writes with it, rather than leaving them selected
        # and silently not loaded — the screen shows what the agent gets.
        cfg.operations = [op for op in cfg.operations if op not in writes]
    save(home, configs)
    return cfg


def _check_key_name(cfg: ConnectorConfig) -> None:
    if cfg.key_in == "header":
        if not _HEADER_NAME.match(cfg.key_name) or cfg.key_name.lower() in _FORBIDDEN_HEADERS:
            raise ConnectorError(f"{cfg.key_name!r} cannot carry a key")
    elif not _QUERY_NAME.match(cfg.key_name):
        raise ConnectorError(f"{cfg.key_name!r} is not a query parameter name")


def remove(home: Path, name: str) -> bool:
    with _LOCK:
        configs = load(home)
        kept = [c for c in configs if c.name != name]
        if len(kept) == len(configs):
            return False
        save(home, kept)
        _spec_path(home, name).unlink(missing_ok=True)
        return True


# ------------------------------------------------------------------ the key


def read_key(env: str, env_path: Path | None = None) -> str:
    """The key's value: the process environment first (what Settings writes live), then ``.env``.

    The ``.env`` fallback exists because a variable Settings has no field for is never exported to
    the environment on a fresh start (`config._export_env_file_credentials` exports provider keys
    only), so a key saved yesterday would read as missing today.
    """
    if not env:
        return ""
    value = os.environ.get(env, "")
    if value:
        return value
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - a dependency of pydantic-settings
        return ""
    path = env_path or Path(".env")
    if not path.is_file():
        return ""
    return str(dotenv_values(path).get(env) or "")


# ------------------------------------------------------------------ the tool


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port


class ConnectorTool(Tool):
    """One operation of an owner-added connector, with the rules this module's docstring lists."""

    #: Remote content, and the name comes from a spec — so the marker, not a name list, is what
    #: makes the taint ledger fence and record it.
    untrusted_output = True

    def __init__(
        self,
        rest: RestApiTool,
        *,
        connector: str,
        name: str,
        key: str = "",
        key_in: str = "header",
        key_name: str = "",
        key_prefix: str = "",
    ) -> None:
        self._rest = rest
        self.connector = connector
        self.name = name
        self.method = rest.method
        self.description = f"[{connector} API, {rest.method} {rest.path_template}] {rest.description}"[:1024]
        self.parameters = rest.parameters
        self._key = key
        self._key_in = key_in
        self._key_name = key_name
        self._key_prefix = key_prefix
        #: The owner's approver, set by the assembly that has a screen to ask on — the same object
        #: the file tools get as ``ask_outside``. None: a non-GET call is refused.
        self.ask_outside: Any = None

    def _url(self, kwargs: dict[str, Any]) -> str:
        path = self._rest.path_template
        for param in self._rest.path_params:
            # Percent-encoded whole: a value is ONE path segment, so `../admin` or `x?y=1#` or
            # `@evil.test` stays a string inside it instead of becoming URL structure.
            path = path.replace("{" + param + "}", quote(str(kwargs.get(param, "")), safe=""))
        return f"{self._rest.base_url}{path}"

    def _leaves_base_path(self, url: str) -> bool:
        """Whether ``url``'s path climbs out of the base URL's path.

        Encoding is not enough on its own: ``quote`` leaves ``.`` alone (it is unreserved), so a
        value of exactly ``..`` is still a dot segment, and httpx resolves dot segments before it
        sends. Measured: base ``…/v1/tenants/42``, template ``/{a}/{b}``, ``a=b=".."`` went to
        ``/v1``. The origin stayed the same, so the origin check passed, and the tenant or project
        scope the owner put in the base path was gone. A dot segment in its encoded spelling
        (``%2e%2e``) is refused too: httpx sends it as is, and a server may decode it.
        """
        path = urlsplit(url).path
        if any(unquote(segment) in (".", "..") for segment in path.split("/")):
            return True
        base = urlsplit(self._rest.base_url).path.rstrip("/")
        return bool(base) and not (path == base or path.startswith(base + "/"))

    def run(self, **kwargs: Any) -> str:
        missing = [p for p in self._rest.path_params if not str(kwargs.get(p, "")).strip()]
        if missing:
            return f"error: missing required path parameter(s): {', '.join(missing)}"
        url = self._url(kwargs)
        if _origin(url) != _origin(self._rest.base_url) or self._leaves_base_path(url):
            return refusal(f"{self.name}: the request would leave {self._rest.base_url}. Do not retry.")
        query = {n: kwargs[n] for n in self._rest.query_params if n in kwargs}
        body = kwargs.get("body") if self._rest.has_body else None
        if self.method != "GET":
            stopped = self._ask(url, query, body)
            if stopped is not None:
                return stopped
        return self._send(url, query, body)

    def _ask(self, url: str, query: dict[str, Any], body: Any) -> str | None:
        """Ask the owner about THIS request — what will be sent, not only where.

        The question used to carry the method and the path and nothing else, so a ``DELETE
        /items?id=…`` or a ``POST`` whose body carried the owner's private data to the API's host
        was approved without the one thing that decides it. The query and the body the model chose
        are in the question now, the body cut at 2 KB and with the key masked.
        """
        from chimera.tools.workspace import BoundaryQuestion

        ask = self.ask_outside
        host = urlsplit(url).hostname or url
        if ask is None:
            return refusal(
                f"{self.name} is a {self.method} on {host}, which can change data there, and only the "
                "owner may approve that — nobody here can be asked. The call did NOT run. Do not retry."
            )
        target = self._mask(self._shown(url, query))
        sent = ""
        if body is not None:
            text = json.dumps(body, ensure_ascii=False, default=str)
            if len(text) > _MAX_ASK_BODY_CHARS:
                text = text[:_MAX_ASK_BODY_CHARS] + f"… [{len(text)} chars in all]"
            sent = f" with body {self._mask(text)}"
        question = BoundaryQuestion(
            reason=(
                f"{self.connector}: {self.method} {target}{sent} — an API call that can change data "
                f"on {host}"
            ),
            action=f"{self.name}: {self.method} {target}{sent}",
        )
        if not ask(question):
            return refusal(
                f"{self.name}: the owner was asked about this {self.method} and said no. "
                "The call did NOT run. Do not retry."
            )
        return None

    def _send(self, url: str, query: dict[str, Any], body: Any) -> str:
        import time

        import httpx

        from chimera.scrape.ssrf import check_url

        base_origin = _origin(self._rest.base_url)
        last = "request failed"
        # A GET may be repeated; anything else is sent ONCE. The owner approved one call, and a
        # retry on a 5xx or a read timeout repeats a request the server may already have acted on —
        # a 502 from a proxy in front of a payment API is not evidence the charge did not happen.
        # The importer's retry policy was written for reads and this class is the one that lets
        # writes through, so it decides here rather than inheriting `retries=2`.
        retries = self._rest.retries if self.method == "GET" else 0
        for attempt in range(retries + 1):
            hop = url
            params: dict[str, Any] | None = dict(query)
            delay = 0.0
            try:
                with httpx.Client(timeout=self._rest.timeout, follow_redirects=False) as client:
                    for _ in range(_MAX_REDIRECTS + 1):
                        check_url(hop)
                        same = _origin(hop) == base_origin
                        headers, sent = self._credentials(params, same=same)
                        with client.stream(
                            self.method, hop, params=sent or None, json=body, headers=headers or None
                        ) as response:
                            if response.is_redirect and response.headers.get("location"):
                                target = str(httpx.URL(hop).join(response.headers["location"]))
                                check_url(target)  # an internal address is an error, as before
                                why = self._redirect_refused(target)
                                if why:
                                    shown = self._shown(hop, query if params is not None else None)
                                    return self._mask(
                                        f"[{response.status_code}] {self.method} {shown}\n"
                                        f"The server redirected to {target} and it was NOT followed: "
                                        f"{why}"
                                    )
                                hop = target
                                params = None  # the Location carries its own query
                                continue
                            if response.status_code in _RETRY_STATUS and attempt < retries:
                                last = f"status {response.status_code}"
                                delay = _retry_delay(
                                    attempt, response.headers.get("Retry-After"),
                                    backoff=self._rest.backoff, cap=self._rest.max_backoff,
                                )
                                break
                            raw = bytearray()
                            for chunk in response.iter_bytes():
                                raw += chunk
                                if len(raw) > _MAX_BODY_BYTES:
                                    break
                            status = response.status_code
                            encoding = response.encoding or "utf-8"
                        return self._report(status, hop, query if params is not None else None, raw, encoding)
                    else:
                        return f"error: {self.name}: too many redirects"
            except ValueError as exc:  # the SSRF guard
                return f"error: {exc}"
            except httpx.HTTPError as exc:
                last = f"request failed: {type(exc).__name__}"
                delay = self._rest.backoff * (2.0**attempt)
            if attempt < retries:
                time.sleep(min(delay, self._rest.max_backoff))
        return f"error: {last}"

    def _redirect_refused(self, target: str) -> str:
        """Why the redirect to ``target`` is not followed, or ``""`` when it may be.

        A call that can change data follows none. The owner approved one request to one URL, and a
        redirect would re-send it somewhere the card never showed: a 307/308 re-sends the method
        AND the body (measured: a POST approved for ``api.pets.example`` carried its body to
        ``cdn.other.example``), and httpx turns a 301/302/303 after a POST into a second request to
        the Location. The 3xx itself is the answer the agent gets — the request already reached
        the server once, and it must not be sent again.

        A GET follows a redirect only while it stays inside what the owner configured: the same
        origin and the same base path. Dropping the key on a cross-origin hop kept the key safe but
        not the promise the Connections screen makes (``Reaches only {url}``), and a same-origin
        hop from ``/v1/tenants/42`` to ``/v1/tenants/99`` carried the key out of the tenant the
        owner scoped it to. The Location is in the answer, so the agent can still open it with a
        tool that is meant to go anywhere — just not with the owner's key.
        """
        if self.method != "GET":
            return (
                f"a {self.method} is sent to the one URL the owner approved, never on to another. "
                "The request reached the server once; do not send it again."
            )
        if _origin(target) != _origin(self._rest.base_url) or self._leaves_base_path(target):
            return f"it leaves {self._rest.base_url}, the only place this connector reaches."
        return ""

    def _credentials(
        self, params: dict[str, Any] | None, *, same: bool
    ) -> tuple[dict[str, str], dict[str, Any] | None]:
        """Headers and query for one hop. The key only ever goes to the configured origin."""
        if not self._key or not same:
            return {}, params
        if self._key_in == "query":
            return {}, {**(params or {}), self._key_name: self._key}
        return {self._key_name: f"{self._key_prefix}{self._key}"}, params

    def _report(
        self, status: int, url: str, query: dict[str, Any] | None, raw: bytearray, encoding: str
    ) -> str:
        # Masked BEFORE it is cut. The other order cut first: a key echoed across the cut was no
        # longer whole, the mask's replace did not match, and the part before the cut reached the
        # model (measured: 19,990 characters of padding, then the key — its first 10 characters
        # arrived in clear). A server that echoes the request chooses the padding, so it chose the
        # prefix. Masking the whole body first leaves the cut nothing of the key to split.
        text = self._mask(bytes(raw).decode(encoding, errors="replace"))
        if len(text) > _MAX_BODY_CHARS:
            text = text[:_MAX_BODY_CHARS] + f"\n... [truncated, {len(text)} chars total]"
        # The URL as the agent asked for it, never with the key a query-style credential added.
        return self._mask(f"[{status}] {self.method} {self._shown(url, query)}\n{text}")

    @staticmethod
    def _shown(url: str, query: dict[str, Any] | None) -> str:
        if not query:
            return url
        import httpx

        return str(httpx.URL(url, params=query))

    def _mask(self, text: str) -> str:
        """``text`` with the key replaced — for an API that echoes its request back.

        In every spelling the request carried it in, not only the raw one: a query-style key that
        holds ``+``, ``/`` or ``=`` (base64 does) travels percent-encoded, and an API that echoes
        its request URL hands it back that way. Measured: ``ab+cd/ef==ghij1234`` came back as
        ``ab%2Bcd%2Fef%3D%3Dghij1234``, unmasked. The encoded forms are matched without regard to
        case, because an echo may lower-case the hex digits (``%2b``).

        A key shorter than six characters is not masked: replacing every occurrence of a
        four-character string corrupts the answer the agent reads, and a credential that short is
        not one this mask could protect anyway.
        """
        if not self._key or len(self._key) < 6:
            return text
        text = text.replace(self._key, _MASK)
        encoded = {quote(self._key, safe=""), quote(self._key), quote_plus(self._key)} - {self._key}
        for form in sorted(encoded, key=len, reverse=True):
            text = re.sub(re.escape(form), _MASK, text, flags=re.IGNORECASE)
        return text


# ------------------------------------------------------------------ assembly


def connector_tools(
    home: Path, *, env_path: Path | None = None, unattended: bool = False
) -> list[ConnectorTool]:
    """The tools of every ENABLED connector, built from its pinned spec. Never touches the network.

    ``unattended`` keeps only the connectors the owner also switched on for the unattended
    surfaces. A connector whose snapshot is missing or broken is skipped with a warning: one broken
    entry must not take the others, or the turn, down with it.
    """
    tools: list[ConnectorTool] = []
    for cfg in load(home):
        if not cfg.enabled or (unattended and not cfg.unattended):
            continue
        try:
            spec = load_snapshot(home, cfg.name)
            selected = set(cfg.operations)
            by_id = {op.id: op for op in operations(spec, cfg.name)}
            key = read_key(cfg.key_env, env_path) if cfg.key_env in allowed_key_envs(cfg.name) else ""
            seen: set[str] = set()
            for rest in tools_from_openapi(spec, base_url=cfg.base_url):
                op = by_id.get(rest.name)
                if op is None or op.id not in selected or op.tool in seen:
                    continue
                if op.method != "GET" and not cfg.allow_writes:
                    continue  # the file was edited by hand into a shape the screen refuses
                seen.add(op.tool)
                tools.append(
                    ConnectorTool(
                        rest, connector=cfg.name, name=op.tool, key=key,
                        key_in=cfg.key_in, key_name=cfg.key_name, key_prefix=cfg.key_prefix,
                    )
                )
        except Exception as exc:  # noqa: BLE001 — a broken connector must never break a turn
            _log.warning("connectors: skipping %r — %s", cfg.name, type(exc).__name__)
    return tools


def mount(
    registry: Any,
    home: Path,
    *,
    ask: Any = None,
    env_path: Path | None = None,
    unattended: bool = False,
) -> int:
    """Register the enabled connectors' tools into ``registry``; returns how many were added.

    Called BEFORE the deployment's allow/deny filter, so ``CHIMERA_TOOL_DENYLIST`` reaches these
    tools by name. A name already in the registry is skipped, never replaced. ``unattended``: only
    the connectors the owner sent to the unattended surfaces (``governed_profile`` passes it).
    """
    count = 0
    for tool in connector_tools(home, env_path=env_path, unattended=unattended):
        if tool.name in registry:
            _log.warning("connectors: %r collides with an existing tool — skipping", tool.name)
            continue
        tool.ask_outside = ask
        registry.register(tool)
        count += 1
    return count


#: The ``governed_profile`` surfaces where a stranger can be on the other end: the bots (the CLI's
#: ``platform``, the app's ``app-messaging``) and ``chimera serve``, which carries the WhatsApp
#: webhook and an HTTP gateway.
MESSAGING_SURFACES = frozenset({"platform", "app-messaging", "serve"})


def unattended_surface_may_load(settings: Any, surface: str) -> str:
    """Why ``surface`` may NOT load connectors, or ``""`` when it may.

    A connector's GET runs with the owner's key, so on a bot it answers whoever the bot answers.
    The allowlists ship empty, and empty means anyone (``chimera.server.allowlist``) — which is the
    owner's call for the bot, and must not silently become the owner's call for their private APIs
    too. While any configured bot is open, no messaging surface loads a connector. Checked across
    every configured bot rather than the one this surface runs: ``serve`` and the app's manager
    can carry more than one, and ``governed_profile`` is not told which.
    """
    if surface not in MESSAGING_SURFACES:
        return ""
    from chimera.server.allowlist import ALLOWLIST_FIELDS, allowed_users_for, bot_configured

    open_bots = [
        platform
        for platform in ALLOWLIST_FIELDS
        if bot_configured(settings, platform) and allowed_users_for(settings, platform) is None
    ]
    if open_bots:
        return f"the {', '.join(open_bots)} bot answers anyone (no allowlist)"
    return ""


def with_connectors(registry: Any, settings: Any, *, ask: Any = None) -> Any:
    """``registry`` with the owner's enabled connectors in it — the one call every assembly makes.

    Wraps a fresh ``default_registry(...)`` at each call site, so whatever governs that surface
    (``governed_profile``, ``assemble_registry``'s lists, the chat guard) wraps these tools too.
    ``ask`` is that surface's owner approver when it has a screen; without one a non-GET call is a
    refusal. Never raises: a broken store must not cost a turn.
    """
    try:
        mount(registry, Path(settings.home), ask=ask)
    except Exception as exc:  # noqa: BLE001
        _log.warning("connectors: not loaded — %s", type(exc).__name__)
    return registry
