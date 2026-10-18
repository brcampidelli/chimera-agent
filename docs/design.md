# Chimera Design: the document domain

Chimera Design turns a description into an editable visual artefact (a landing page, a slide, a
post) and refines it in conversation. This page describes the domain package, `chimera/design/`.
The API routes, the agent tool and the desktop canvas are built on top of it in later changes. None
of them exist yet.

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
- Storage has no `designs` category yet, so the folder is counted under "other" until the API change
  adds it.
