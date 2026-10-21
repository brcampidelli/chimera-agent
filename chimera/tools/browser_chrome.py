"""Read-only Chrome extension browser driver, backed by an injected JSON transport."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable
from html import escape
from pathlib import Path
from typing import Protocol

from chimera.tools.browser import BrowserFrame, Element

#: Provider frames commonly used to collect payment details.
_PAYMENT_FRAME_PATTERNS = ("stripe", "adyen", "pagseguro", "mercadopago", "paypal")
_SENSITIVE_FIELD = re.compile(
    r"password|senha|passwort|contrase[nñ]a|mot\s+de\s+passe|passcode|secret|"
    r"cvv|cvc|csc|card\s*number|security\s+code|c[oó]digo\s+de\s+seguran|n[uú]mero\s+do\s+cart[aã]o|"
    r"n[uú]mero\s+de\s+tarjeta|kartennummer|\bpin\b|token|2fa|otp|"
    r"c[oó]digo\s+de\s+verifica|verification\s+code",
    re.IGNORECASE,
)
_DOWNLOAD_SUFFIXES = (
    ".exe", ".msi", ".zip", ".dmg", ".apk", ".pkg", ".bat", ".js", ".deb", ".iso", ".rar", ".7z",
)
_SENSITIVE_AUTOCOMPLETE = {"one-time-code", "current-password", "new-password"}


class ChromeTransport(Protocol):
    def send(self, message: dict[str, object]) -> dict[str, object]: ...


class ChromeExtensionDriver:
    """Synchronous BrowserDriver facade over a request/reply native-messaging transport.

    The extension performs CDP calls; the transport implementation belongs to the pairing layer and
    is injected so this driver can be exercised without starting Chrome.
    """

    def __init__(self, transport: ChromeTransport) -> None:
        self._transport = transport
        self._request_id = 0
        self._closed = False
        self._backend_nodes: dict[str, int] = {}
        self._elements_by_ref: dict[str, Element] = {}
        self._pending_audit: tuple[str, str, str, str, str | bool | None, int] | None = None
        self.ask_action: Callable[[object], bool] | None = None
        self.audit: object = None

    def _request(self, operation: str, **payload: object) -> object:
        if self._closed and operation != "close":
            raise RuntimeError("Chrome extension browser is closed")
        self._request_id += 1
        request_id = self._request_id
        reply = self._transport.send({"id": request_id, "op": operation, **payload})
        if reply.get("id") != request_id or not isinstance(reply.get("ok"), bool):
            raise RuntimeError("Chrome extension returned an invalid reply")
        if reply["ok"] is False or "error" in reply:
            error = reply.get("error")
            raise RuntimeError(error if isinstance(error, str) else "Chrome extension request failed")
        if "result" not in reply:
            raise RuntimeError("Chrome extension returned an invalid reply")
        return reply["result"]

    def _elements(self, tree: object) -> list[Element]:
        nodes_obj = tree.get("nodes", []) if isinstance(tree, dict) else tree
        if not isinstance(nodes_obj, list):
            raise ValueError("Chrome extension returned an invalid accessibility tree")
        interactive = {
            "button", "checkbox", "combobox", "link", "menuitem", "option", "radio",
            "searchbox", "slider", "spinbutton", "switch", "tab", "textbox", "treeitem",
        }
        result: list[Element] = []
        backend_nodes: dict[str, int] = {}
        for node in nodes_obj:
            if not isinstance(node, dict):
                continue
            role = _ax_string(node.get("role"))
            if role not in interactive:
                continue
            name = _ax_string(node.get("name"))
            properties = node.get("properties", [])
            if isinstance(properties, list) and any(
                isinstance(prop, dict)
                and str(_ax_string(prop.get("name"))).lower() in {"password", "protected"}
                and _ax_string(prop.get("value")).lower() in {"true", "1"}
                for prop in properties
            ):
                name = f"{name} (password)" if name else "(password)"
            ref = f"e{len(result) + 1}"
            backend_id = node.get("backendDOMNodeId")
            if isinstance(backend_id, int):
                backend_nodes[ref] = backend_id
            result.append(Element(ref=ref, role=role, name=name[:120]))
        self._backend_nodes = backend_nodes
        self._elements_by_ref = {element.ref: element for element in result}
        return result

    def navigate(self, url: str) -> list[Element]:
        self._request("navigate", url=url)
        return self.read()

    def read(self) -> list[Element]:
        return self._elements(self._request("read"))

    def _backend_node(self, ref: str) -> int:
        node = self._backend_nodes.get(ref)
        if node is None:
            raise KeyError(f"unknown or stale ref {ref!r}; read the page again")
        return node

    def describe(self, ref: str) -> dict[str, object]:
        result = self._request("describe", ref_node=self._backend_node(ref))
        if not isinstance(result, dict):
            raise ValueError("Chrome extension returned invalid target description")
        return {**result, "ref": ref}

    def click(self, ref: str) -> list[Element]:
        self._backend_node(ref)
        description = self.describe(ref)
        self._guard_action("click", description)
        try:
            result = self._request(
                "click", ref_node=self._backend_node(ref), expected=_target_summary(description),
                allowlisted=self._pending_audit is not None and self._pending_audit[4] is None,
            )
        except Exception:
            self._finish_audit("error")
            raise
        elements = self._action_elements(result)
        self._finish_audit("ok")
        return elements

    def type_text(self, ref: str, text: str) -> list[Element]:
        self._backend_node(ref)
        description = self.describe(ref)
        self._guard_action("type", description, text=text)
        try:
            result = self._request(
                "type", ref_node=self._backend_node(ref), text=text, expected=_target_summary(description),
            )
        except Exception:
            self._finish_audit("error")
            raise
        elements = self._action_elements(result)
        self._finish_audit("ok")
        return elements

    def _finish_audit(self, result: str) -> None:
        pending = getattr(self, "_pending_audit", None)
        if pending is None:
            return
        host, action, role, name, approved_by, text_length = pending
        self._pending_audit = None
        audit = getattr(self, "audit", None)
        if audit is not None:
            try:
                audit.record(
                    "browser.action",
                    {
                        "host": host,
                        "action": action,
                        "role": role,
                        "name": name,
                        "result": result,
                        "approved_by": approved_by,
                        **({"text_length": text_length} if action == "type" else {}),
                    },
                )
            except Exception:  # noqa: BLE001 — audit failure cannot change an action result
                return

    def _action_elements(self, result: object) -> list[Element]:
        if not isinstance(result, dict) or "elements" not in result:
            raise ValueError("Chrome extension returned invalid action result")
        return self._elements(result["elements"])

    def _guard_action(
        self, action: str, target: dict[str, object], *, text: str = ""
    ) -> None:
        from urllib.parse import urlsplit

        host = ""
        try:
            current = urlsplit(self.current_url())
            host = (current.hostname or "").lower()
        except Exception as exc:  # noqa: BLE001 — do not act without origin confirmation
            self._record_action("", action, "", "", "refused", None, text)
            raise RuntimeError("Could not verify browser origin; the action did not run.") from exc
        element = self._elements_by_ref.get(str(target.get("ref", "")))
        role = _field(target, "role") or (element.role if element is not None else "")
        name = _field(target, "accessibleName") or (element.name if element is not None else "")
        identity_values = [
            _field(target, key)
            for key in (
                "name", "id", "ariaLabel", "label", "placeholder", "accessibleName",
                "axName", "resolvedAccessibleName",
            )
        ]
        identity_values.extend(_strings(target.get("labels")))
        identity_values.append(_field(target, "text"))
        identity_values.append(_field(target, "frameUrl"))
        if element is not None:
            identity_values.append(element.name)
        autocomplete_tokens = _field(target, "autocomplete").casefold().split()
        tag = _field(target, "tag").casefold()
        input_type = _field(target, "type").casefold()
        frame_url = _field(target, "frameUrl").casefold()
        is_payment_frame = any(marker in frame_url for marker in _PAYMENT_FRAME_PATTERNS)
        sensitive = (
            (tag == "input" and input_type == "password")
            or any(token.startswith("cc-") or token in _SENSITIVE_AUTOCOMPLETE for token in autocomplete_tokens)
            or any(_SENSITIVE_FIELD.search(value) for value in identity_values)
            or is_payment_frame
        )
        if sensitive:
            self._refuse(host, action, role, name, text, "sensitive target or payment frame")
        if _page_is_challenge(self):
            self._refuse(host, action, role, name, text, "CAPTCHA or robot verification detected")
        if _download_or_upload(target):
            self._refuse(host, action, role, name, text, "downloads and uploads are disabled")

        allowed = False
        if action == "click" and tag == "a":
            allowed = _safe_same_host_link(target, current.hostname or "")
        elif action == "type":
            role_name = role.casefold()
            allowed = bool(name.strip()) and (
                role_name == "searchbox" or (tag == "input" and input_type == "search")
            )
        approved_by: str | bool | None = None
        if not allowed:
            approver = getattr(self, "ask_action", None)
            if not callable(approver):
                self._refuse(host, action, role, name, text, "owner approval required")
            assert callable(approver)
            question = type("BrowserActionQuestion", (), {})()
            question.reason = f"Approve browser {action} on {host or 'unknown host'}"
            question.action = f"{action}: {role or 'unknown role'}: {name[:120]}"
            if action == "click" and tag == "a":
                question.action += f" -> {_field(target, 'href')[:200]}"
            if action == "type":
                question.action += f" (text length {len(text)})"
            if not approver(question):
                self._record_action(host, action, role, name, "refused", False, text)
                raise RuntimeError("Owner refused browser action; the action did not run; do not retry.")
            approved_by = "owner"
        self._pending_audit = (host, action, role, name[:120], approved_by, len(text))

    def _refuse(
        self, host: str, action: str, role: str, name: str, text: str, reason: str
    ) -> None:
        self._record_action(host, action, role, name, "refused", None, text)
        raise RuntimeError(f"{reason}. The browser action did not run; do not retry.")

    def _record_action(
        self, host: str, action: str, role: str, name: str, result: str,
        approved_by: str | bool | None, text: str,
    ) -> None:
        audit = getattr(self, "audit", None)
        if audit is not None:
            try:
                audit.record(
                    "browser.action",
                    {
                        "host": host,
                        "action": action,
                        "role": role,
                        "name": name[:120],
                        "result": result,
                        "approved_by": approved_by,
                        **({"text_length": len(text)} if action == "type" else {}),
                    },
                )
            except Exception:  # noqa: BLE001 — audit failure cannot change a safety decision
                return

    def back(self) -> list[Element]:
        self._request("back")
        return self.read()

    def page_html(self) -> str:
        # The extension deliberately exposes only rendered body text, never page source/DOM.
        return "<html><body>" + _html_escape(self.page_text()) + "</body></html>"

    def current_url(self) -> str:
        result = self._request("current_url")
        if not isinstance(result, str):
            raise ValueError("Chrome extension returned invalid current URL")
        return result

    def google_results(self) -> list[dict[str, str]]:
        result = self._request("google_results")
        if not isinstance(result, list):
            raise ValueError("Chrome extension returned invalid Google results")
        rows: list[dict[str, str]] = []
        for item in result[:10]:
            if isinstance(item, dict) and all(
                isinstance(item.get(key), str) for key in ("title", "link", "snippet")
            ):
                rows.append({key: item[key] for key in ("title", "link", "snippet")})
        return rows

    def page_text(self) -> str:
        result = self._request("read_text")
        if not isinstance(result, str):
            raise ValueError("Chrome extension returned invalid page text")
        return result

    def screenshot(self, path: str) -> None:
        result = self._request("screenshot")
        if not isinstance(result, str):
            raise ValueError("Chrome extension returned invalid screenshot data")
        try:
            image = base64.b64decode(result, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Chrome extension returned invalid screenshot data") from exc
        Path(path).write_bytes(image)

    def frame(self) -> BrowserFrame | None:
        return None

    def close(self) -> None:
        if self._closed:
            return
        self._request("close")
        self._closed = True

def _field(value: dict[str, object], key: str) -> str:
    item = value.get(key, "")
    return str(item) if isinstance(item, (str, int, float)) else ""


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _safe_same_host_link(target: dict[str, object], host: str) -> bool:
    from urllib.parse import urlsplit

    href = _field(target, "href")
    try:
        parsed = urlsplit(href)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.hostname.casefold() != host.casefold():
        return False
    if parsed.query or parsed.fragment or not re.fullmatch(r"/[A-Za-z0-9._~/-]*", parsed.path):
        return False
    if target.get("download") or _field(target, "target") or _field(target, "ping"):
        return False
    if target.get("hasForm") or target.get("hasEventListeners") is not False:
        return False
    if _DOWNLOAD_SUFFIXES and parsed.path.casefold().endswith(_DOWNLOAD_SUFFIXES):
        return False
    return not re.search(
        r"revoke|disconnect|approve|unfollow|trash|close|deactivate|cancel|delete|remove|"
        r"excluir|logout|sair|unsubscribe|download", parsed.path, re.I
    )


def _target_summary(target: dict[str, object]) -> str:
    return json.dumps(
        [
            _field(target, "tag"),
            _field(target, "type"),
            _field(target, "autocomplete"),
            _field(target, "axName") or _field(target, "accessibleName"),
            _field(target, "href"), _field(target, "target"), bool(target.get("download")),
            bool(target.get("hasForm")), _field(target, "formMethod"),
            target.get("hasEventListeners"), _field(target, "ping"), _field(target, "frameUrl"),
        ],
        ensure_ascii=False,
    )


def _download_or_upload(target: dict[str, object]) -> bool:
    tag = _field(target, "tag").casefold()
    input_type = _field(target, "type").casefold()
    if tag == "input" and input_type == "file":
        return True
    if tag == "label" and target.get("forFileInput") is True:
        return True
    href = _field(target, "href").split("?", 1)[0].split("#", 1)[0].casefold()
    link_like = tag == "a" or bool(_field(target, "href"))
    return link_like and (bool(target.get("download")) or href.endswith(_DOWNLOAD_SUFFIXES))


def _page_is_challenge(driver: ChromeExtensionDriver) -> bool:
    try:
        from chimera.tools.browser import _google_challenge

        page_text = driver.page_text()
        opening = "\n".join(page_text.splitlines()[:40]).casefold()
        challenge_markers = (
            "recaptcha", "hcaptcha", "turnstile", "just a moment", "verify you are human",
            "verifique que você é humano", "verifique que voce e humano", "robot check",
        )
        if _google_challenge(page_text, driver.current_url()) or any(
            marker in opening for marker in challenge_markers
        ):
            return True
        frames = driver._request("challenge_frames")
        return isinstance(frames, list) and any(
            re.search(r"recaptcha|hcaptcha|turnstile|challenges\.cloudflare", str(url), re.IGNORECASE)
            for url in frames
        )
    except Exception:  # noqa: BLE001 — if challenge inspection fails, fail closed
        return True


def ref_label(target: dict[str, object]) -> str:
    return _field(target, "role")


def _ax_string(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("value")
    return str(value) if isinstance(value, (str, int, float)) else ""


def _html_escape(value: str) -> str:
    return escape(value)


__all__ = ["ChromeExtensionDriver", "ChromeTransport"]
