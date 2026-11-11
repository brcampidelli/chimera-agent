# Picking an element of the preview page

The Code screen's Preview panel frames the dev server's page in an iframe with an opaque origin (no
`allow-same-origin`), so nothing is injected into it and the screen cannot ask it what is under a click.
"Select element" (Ctrl/Cmd+Shift+S) therefore works on the server side, in the browser Chimera already has.

## The path

1. `POST /api/preview/select/capture`: opens the page in the app's Playwright browser and returns a JPEG
   of the viewport, its size, and an internal revision (address plus load time of the document).
2. The owner clicks the picture. The client sends the point in the page's own pixels.
3. `POST /api/preview/select/pick`: asks the SAME page for the element at that point
   (`document.elementFromPoint`), refuses if the loaded document is no longer the one in the picture, and
   returns the element. Its text is stored as an attachment (sanitized and data-fenced by
   `chimera/api/attachments.py`); the crop of the element is stored as an image attachment.
4. Both attachments are marked as page content (`attachment-origin/<id>` under the data folder). The turn
   that carries either one records a fetch in its ledger (`record_page_elements` for the text, which also
   carries a mark of its own, and `record_page_origin` for any attachment, including the PNG, which has no
   text to mark), so the run is tainted like any other page read.
5. If no conversation screen is mounted to take the chips, nothing is added, the panel says so, and
   `POST /api/preview/select/discard` deletes what was stored (only attachments marked as page content).
6. `POST /api/preview/select/close` ends the session. An idle session is closed by a timer (5 minutes), a
   close cancels a capture still loading (generation counter), and a browser that does not answer in 90 s is
   abandoned: the caller gets a 504 and the next call runs on a fresh thread.

## Rules

* Owner's desktop screen only (the guard of `/api/preview`); the routes are absent from the bridge table.
* A configured process server that is running, whose own process tree listens on its port, and whose port is
  none of Chimera's. A server attached by `url` is never opened.
* The browser is built from the app's own settings (site list, headless switch), except the cookie profile:
  it is always ephemeral, whatever `CHIMERA_BROWSER_COOKIE_RETENTION` says, because the project's page must
  not run in the profile that holds the agent's sign-ins.
* The page's script runs in the page and can replace what the reader calls. Nothing it returns is trusted:
  `validate` keeps only an allowlist of attributes, requires a tag that is a plain name, clips every field,
  never reads a form field's value, and drops the query, fragment and credentials of every address.
* A path segment of an address that looks like a credential (20+ hex characters, or 20+ url-safe base64
  characters mixing upper case, lower case and digits) is replaced with `[redacted]`. This is a heuristic: a
  secret in a readable segment, or in the element's visible text, is not recognised. The text is the
  page's, which is why it is fenced and the turn is tainted.

## Tests

`tests/test_element_pick.py` (server, validation, taint, races, timeouts; includes a hostile-page check that
never skips and a Node run of the real reader), `tests/test_element_pick_browser.py` (real Chromium, skipped
without one), and the `PreviewPane` and `attach-request` tests on the desktop side.
