"""Read-only Chrome extension browser driver, backed by an injected JSON transport."""

from __future__ import annotations

import base64
import binascii
from html import escape
from pathlib import Path
from typing import Protocol

from chimera.tools.browser import BrowserFrame, Element


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
        return result

    def navigate(self, url: str) -> list[Element]:
        self._request("navigate", url=url)
        return self.read()

    def read(self) -> list[Element]:
        return self._elements(self._request("read"))

    def click(self, ref: str) -> list[Element]:
        raise RuntimeError("click is not available until E2")

    def type_text(self, ref: str, text: str) -> list[Element]:
        raise RuntimeError("type_text is not available until E2")

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


def _ax_string(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("value")
    return str(value) if isinstance(value, (str, int, float)) else ""


def _html_escape(value: str) -> str:
    return escape(value)


__all__ = ["ChromeExtensionDriver", "ChromeTransport"]
