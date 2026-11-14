(// What the webview runs before every document: hands the page the app's two credentials. Rust wraps
// this expression and calls it with four JSON literals (`init_script` in main.rs); the Vitest test in
// `src/lib/credentialInit.test.ts` evaluates this same file, so the two cannot drift apart.
//
// It runs before ANY document, not only the app's own (P0-19): Tauri's initialization script follows
// the window wherever it navigates, and on Windows (WebView2) it is also added to every subframe —
// the dev-server preview, the Design preview, an HTML artboard. So it gives the credentials to the
// top-level document of the app's own scheme and host, and to nothing else.
//
// The port is not checked here because it can move: when the backend comes back on another port the
// shell navigates the window there, and this script, fixed when the window was built, cannot learn
// the new one. The window's `on_navigation` holds the top level to the current origin instead, so
// "top frame on this scheme and host" is "the app" by the time this runs.
//
// And only to the app's own entry page, not to every document of the origin: `/guest.html` (the
// share-link page, built beside the app) is served there too without a token, and a relative link
// in a reply reaches it on the same origin. So the path must be `/`, `/index.html`, or a route the
// backend answers with the app itself — segments of letters, digits, `-` and `_` only (no `.`, so
// no file; no `%`, so no encoded dot), and never under `/guest`, `/api` or
// `/v1` (the OpenAI-compatible API, `chimera/api/openai_compat.py`). `location.pathname` is
// already the browser's normalised form (`/./x` resolved, case kept, escapes kept). The same rule is
// `is_app_entry_path` in main.rs, which holds the window's top level to it.
(function (appProtocol, appHostname, serverToken, screenToken) {
  // `window.top` is unforgeable, and nothing on the page has run yet to tamper with `window`.
  if (window !== window.top) return;
  if (window.location.protocol !== appProtocol || window.location.hostname !== appHostname) return;
  var path = window.location.pathname;
  if (path !== "/" && path !== "/index.html") {
    var segments = path.split("/").slice(1);
    for (var i = 0; i < segments.length; i++) {
      if (!/^[A-Za-z0-9_-]*$/.test(segments[i])) return;
    }
    if (/^(guest|api|v1)/i.test(segments[0])) return;
  }
  window.__CHIMERA_SERVER_TOKEN__ = serverToken;
  window.__CHIMERA_SCREEN_TOKEN__ = screenToken;
}))("http:", "127.0.0.1", "server-secret", "screen-secret");
