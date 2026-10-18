# Chimera Design: the document domain

Chimera Design turns a description into an editable visual artefact (a landing page, a slide, a
post) and refines it in conversation. This page describes the domain package, `chimera/design/`.
The HTTP routes (`/api/design`), the agent tool and the desktop's canvas, all described below, are
built on top of it.

## The document

A design document is JSON validated by Pydantic models in `chimera/design/model.py`:

- **Pages** hold **artboards**. An artboard has a size, a background colour, and the ids of its
  top-level elements in paint order.
- **Elements** (`nodes`) are `text`, `shape`, `image` (a local asset id, never a URL) and `group`
  (an ordered list of child ids). A shape is a rectangle, ellipse, line or path. Any shape may carry
  `points`, a flat `[x0, y0, x1, y1, ...]` list in 0..1 of its frame, so resizing scales the drawing.
  A line has exactly two points, which keep its direction.
- **Tokens** are the design system: colours, typography, spacing, radii, and `components`, a
  semantic map such as `button.primary -> {fill: primary, font: label}` that names tokens.
- **Comments** are pinned to a point (`x`, `y`) on an artboard and may name an element:
  `{id, artboardId, nodeId?, x, y, text, author, resolved, createdAt}`.

The JSON uses camelCase keys, identical to the desktop's TypeScript model in
`apps/desktop/src/lib/design/model.ts`. Python fields are snake_case with camelCase aliases, and
`model.wire()` serialises with the aliases and leaves out optional fields that are `None`.

Each element is stored once and placed exactly once, either on an artboard or in a group. Frames use
artboard coordinates, including inside groups. The validator rejects a document with an orphan, a
double placement, a dangling reference or a cycle of groups. It also rejects unknown keys, strings
where numbers belong, colours other than `#RRGGBB`/`#RRGGBBAA`, and anything over the limits in
`model.py`.

There is no owner field. Chimera serves one owner, and an unchecked owner id would suggest access
control that does not exist.

## Operations

All changes go through `apply_operations(doc, ops)` in `ops.py`:

| Operation | What it does |
|---|---|
| `document.rename` | Sets the title. |
| `page.add` / `page.rename` / `page.delete` | Deleting a page removes its contents. |
| `artboard.add` / `artboard.update` / `artboard.delete` | `update` takes `pageId`, `artboardId` and a patch of name, size or background. |
| `node.add` | Takes `pageId`, `artboardId` and an optional `index` (stacking slot). Server extras: `parentId` adds into a group, and a new group brings its descendants in `children`. |
| `node.update` | Patches only the keys allowed for that element type, never `id` or `type`. `null` removes an optional field; required fields refuse it. |
| `node.move` | Sets a frame. `withChildren: true` also moves a group's contents. The default moves only the element, as on the desktop. |
| `node.delete` | Deleting a group deletes its contents. The inverse carries the element's `index`. |
| `node.duplicate` | Copies an element, and a group's contents, under the given `newIds`. |
| `tokens.replace` | Replaces the design system. |
| `comment.add` / `comment.resolve` / `comment.delete` | `resolve` takes `commentId` and `resolved`. |

Type names and keys match the desktop's operations. The server accepts the desktop's payloads
unchanged, and the extras above are optional keys. `page.rename` and `node.duplicate` are server-only
for now.

The engine works on a copy and validates the whole result, so a batch either applies completely or
leaves the document unchanged. The error names the operation that failed. Each call also returns
the inverse operations. Applying them restores the original exactly, and the tests check this for
every operation type. Locked elements refuse moves, deletes and edits. `allow_lock_changes=False`,
which is used for anything a model proposes, also refuses locking and unlocking.

A model has to name the elements it creates. `ids.remap_new_ids` replaces each of those names with
a server-generated id and rewrites the later references in the same proposal. A proposal therefore
cannot reuse the id of a deleted element and pick up that element's comments.

## Storage

`DesignStore` keeps `<home>/designs/<id>/doc.json` and one snapshot per saved revision under
`versions/`, retaining the newest 50. A save states the revision it started from. If the document
has moved on, the save raises `DesignConflict`. The check and the write share one exclusive file
lock, writes are atomic, and strings are redacted before they reach disk. `restore` writes an old
revision as a new one and never rewrites history. Stored documents pass through `migrate.py`. A
document from a newer schema is refused rather than read and silently truncated.

## Export and checks

`render_html` produces one self-contained page. Every string is escaped, the page has no script or
event attribute, and images are embedded only as `data:` URIs built from bytes that
`assets.validate_asset` identifies again. A restrictive Content-Security-Policy is a second barrier.
`check_against_tokens` reports:

- colours that are not token colours;
- text without a typography token;
- text whose contrast against its artboard falls below WCAG AA;
- elements extending past the artboard.

Imported assets must be PNG, JPEG or WebP, identified by their bytes and kept within size and pixel
caps. SVG is refused because it can carry script.

## Asking a model

`propose`, `refine` and `audit` in `propose.py` take any backend with a `complete` method (the
`SupportsComplete` protocol). Each call sends the base instruction and a compact document summary.
The brief, the request, comments and canvas text are wrapped in the data fence as untrusted input.
The reply is parsed as JSON and validated strictly. If it fails, the model is asked again once, with
the error. The proposal's ids are then remapped and its operations applied in a dry run, with lock
changes refused. These functions never save. They return the proposal, a preview and the inverse,
or the reason the proposal could not be used.

Known limits:

- The contrast check compares text with the artboard background only, not with a shape under the
  text.
- "Outside the artboard" ignores rotation.
- Export writes HTML only, and draws images as labelled placeholders: there is no asset store yet.
  PDF and PPTX are refused with a message saying so.

## HTTP routes

`chimera/api/design_api.py` mounts these, behind the server's guard:

| Route | What it does |
|---|---|
| `GET /api/design` | Every readable design: id, title, `updated_at`, revision. |
| `POST /api/design` | Creates an empty design. Optional `title`, `design_system_id`, `tokens`; the id is the server's. |
| `GET /api/design/{id}` | The document in the wire format. |
| `POST /api/design/{id}/ops` | Applies a batch against `expected_revision`. Returns the saved document and the inverse. 409 if the design moved on, 422 for an operation the domain refuses. `origin: "proposal"` holds the batch to a model draft's limits (no lock changes, at most 200 operations). |
| `GET /api/design/{id}/versions` | Saved revisions, newest first. |
| `POST /api/design/{id}/restore` | `{revision, confirm: true}`. Writes the old content as a new revision. Refused without `confirm`. |
| `DELETE /api/design/{id}` | Removes the design and its history. Needs `confirm: true`, in the body or the query. |
| `POST /api/design/{id}/propose`, `/refine`, `/audit` | Ask the configured model. The call is metered under the `design` label and recorded in the usage log. Proposals come back validated and dry-run, with a preview, and are **not applied**: the client applies them through `/ops`, so the owner sees them first. |
| `GET /api/design/{id}/check` | The token checks above. No model. |
| `POST /api/design/{id}/export` | `{format: "html", confirm: true}`. Returns the page as a download: `Content-Disposition: attachment`, `nosniff`, and the exporter's CSP as a header. |

Every route that changes or exports a design, and every model call, records an event on
`audit.jsonl` with ids and counts only. Document text is never logged.

On the desktop bridge, the reads, `check`, `ops`, `propose`, `refine` and `audit` are at the
`operate` tier. Restore, delete and export are absent at every tier. Their `confirm: true` is the
person's answer on the canvas, and a client that sets it for itself has answered its own question.

## When a turn gets the tool

`intent.py` reads each Code turn's message before the agent is built, with no model. It lower-cases
and strips accents, matches whole words only, and answers one of three levels:

- `explicit`: the message names Chimera Design or asks to use, open or present through it ("abre o
  Chimera Design", "mostra o plano em design", "present this as a design", "usa el design").
- `suggest`: the word "design" (or `diseño`) together with a visual word such as landing, screen,
  slide, layout, post, banner, logo, mockup, wireframe, presentation, poster, flyer, UI, interface or
  prototype. Technical uses ("design pattern", "system design", "o design do banco de dados") are
  removed before the visual word is looked for.
- `none`: everything else. The tool is not built and the turn is unchanged.

On `explicit` and `suggest` the turn registers the `design` tool (`chimera/tools/design.py`) before
the denial lists, so `CHIMERA_TOOL_DENYLIST=design` still removes it. When the tool survives the
lists, a short note in the turn context tells the model it is there. A share link's turn never gets
it.

The tool writes only through the store. A `read_only` posture leaves it able to read and propose but
not to save or export. `export_html` writes into `design-exports/` in the project and never replaces
a file. On a linked chat it returns a text summary instead, because a bot never receives HTML.

## Frames on the Code turn's stream

Both frames are kept in the run log, so a screen that reconnects gets them back.

| Event | When | Payload |
|---|---|---|
| `design_suggestion` | once per turn whose message fires the detector | `{level: "explicit" \| "suggest", reason, seq}` |
| `design` | after each saved change the tool made | `{document_id, title, revision, ops_applied, action, seq}` |

`reason` is a stable code: `named`, `asked_for_the_tool`, `present_as_design`, `plan_as_design` or
`design_with_visual_signal`. `action` is `create`, `apply`, `propose` or `export_html`. A proposal
the posture kept from being saved still sends `design` with `ops_applied: 0` and the old revision.
The desktop reads both, as described next.

## On the desktop

Chimera Design opens in the Code screen's viewer, the slot a file opens in: the conversation on the
left, the canvas on the right. It has no rail icon. The palette's "Open design" and "New design", a
card in the conversation, or the agent's own `design` frame bring it up. Below the `lg` breakpoint
the conversation and the canvas take turns, with a switch between them. Both stay mounted, so a
draft in the composer and the canvas's undo history survive the switch. The canvas
(`components/design/DesignPane.tsx`) is loaded on demand: a conversation that never opens a design
never downloads it.

- **Cards.** `design_suggestion` adds a card offering "Open in Chimera Design". On `explicit` the
  viewer also opens at once, showing the list of designs with the message as the brief. "New design
  from this brief" creates one and carries the brief into the design panel. If the agent creates a
  design in that turn first, the open list becomes that design, so no second one is made. Each
  `design` frame adds a compact card (title, revision, what was done) that opens the design. A
  guest's turn gets the cards but never opens the canvas.
- **Autosave** (`useDesignSession`). Every edit applies locally first and is queued. Some 600 ms
  after the last edit the queue goes to `/ops` against the revision the canvas last saw. The header
  says whether the design is saving, saved (with the revision), in conflict or offline. On 409 the
  canvas reads the newer design and replays its unsaved operations on top of it (`lib/design/sync.ts`).
  If they still validate, the merge is saved. If one no longer applies, nothing of the replay is
  kept: the canvas shows the server's copy and says so. A 422 is handled the same way. A network
  failure keeps the queue and retries, on a timer and on the browser's `online` event.
- **Undo across the person and the agent.** The history is kept by the session, not the canvas. An
  agent edit (a `design` frame), a restored version or an applied proposal is one step on the same
  stack as a drag. The app never holds the agent's operations, so an agent step records the
  revisions on either side. Undoing it restores the earlier revision through `/restore`, which
  writes it as a new revision and never rewrites history. The person's undo is the person's answer,
  so it sends `confirm: true`. Only the newest 50 revisions are kept, and an undo that reaches past
  them says so instead of guessing.
- **The design panel**, tabs beside the inspector. *Assistant*: the brief and "Propose", "Refine
  selection" (the selected ids go to `/refine`) and "Audit". A proposal shows its summary,
  assumptions, checks and a still preview of the dry run. It is applied only when the owner presses
  Apply, through `/ops` with `origin: "proposal"`. *Tokens*: colours (strict hex only), spacing and
  radii, each change one `tokens.replace` that keeps the server's `components`. "Check against
  tokens" lists `/check`'s findings, each with a way to the element. *Properties*: the inspector,
  including the selected element's comments and a field to add one.
- **Versions and export** ask first. Restoring names the revision in a confirm step. Exporting
  states that the file leaves the app, lets the person pick a format and downloads the file the
  route returns. The formats come from one constant in `DesignDialogs.tsx`, HTML today, so the
  export PR adds a format by adding a row.

The client's operations (`lib/design/ops.ts`) agree with the server's about groups: deleting a group
deletes its contents, an element inside a group can be deleted on its own, and `node.add` takes
`parentId` and `children`. The TypeScript document has no `ownerId`.
