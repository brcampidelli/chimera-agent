# Chimera desktop — design rules

Written for humans and for agents. Every rule here that can be checked mechanically **is** checked,
by `src/design/design-system.test.ts`, which runs in the normal test step. If you break one, the
suite tells you before review does.

A guideline nobody enforces decays into decoration. This project already ran that experiment: a
coherent design intent lived in a six-line CSS comment while ~150 arbitrary values accumulated
around it.

---

## Principles

1. **Agent-first.** Left is what exists, centre is what I'm doing, right is what the agent is doing
   right now. The agent's state is never hidden by navigating away from it.
2. **Quiet by default, one loud moment.** There is exactly one piece of choreography in this app —
   the launch sequence. Everything else is a 120–200ms state change. Scattered micro-animation is
   what makes software feel cheap; concentration is what makes one moment land.
3. **Tokens, not values.** If you are typing a number or a colour into a class name, the design
   system is missing something. Add the token, don't inline the value.
4. **Motion is `transform` and `opacity`.** Nothing else. Those two are the only properties the
   compositor can animate without touching layout or paint.
5. **Reduced motion is a design state, not a fallback.** It gets its own designed behaviour, not the
   absence of behaviour.

---

## Tokens

All tokens are CSS custom properties in `src/index.css`, surfaced as Tailwind utilities in
`tailwind.config.js`. Both themes are declared exactly once, keyed on `data-theme`.

### Colour — semantics matter more than values

| Token | Utility | Use it for |
|---|---|---|
| `--background` | `bg-background` | the page ground |
| `--card` | `bg-card` | a panel's fill |
| `--surface-2` | `bg-surface-2` | a panel **inside** a panel; a table header |
| `--surface-hover` | `hover:bg-surface-hover` | the fill a row takes under the pointer |
| `--hairline` | `border-hairline` | **the** separator between surfaces |
| `--border` | `border-border` | a stronger, deliberate edge |
| `--muted` / `--muted-foreground` | `bg-muted` / `text-muted-foreground` | secondary text and chips |
| `--accent` / `--accent2` | `text-accent`, `bg-accent-grad` | the brand blue→cyan; one gradient, used sparingly |
| `--ok` / `--warn` / `--bad` | `text-ok`, `bg-warn/15`, `ring-bad/25` | status. Always the token — a literal amber has no light-theme counterpart |
| `--ring` | `shadow-glow` | the focus ring |

> **Never** write `border-white/5`. It is invisible on a white card, so the light theme loses its
> edges entirely. That is a bug, not a style preference. Use `border-hairline`.

### Elevation — earned, not decorative

Six shadows exist (`--elev`, `--elev-lg`, `--inset`, `--glow`, `--btn-shadow`, `--btn-shadow-hover`).
A drop shadow is a depth cue and it **lies** when nothing is actually in front.

- `.surface` — a panel. Flat `--card` fill + hairline. **No shadow.** It sits on the page.
- `.floating` — dialog, command palette, toast, popover. `--elev-lg`. It genuinely overlays.
- `.field` — an input. `--inset`, because it is recessed.

### Type — five sizes, no more

| Utility | Size | Use it for |
|---|---|---|
| `text-xs` | 11px | metadata, badges, captions |
| `text-sm` | 13px | the UI default: labels, table cells, buttons |
| `text-base` | 15px | prose and chat body |
| `text-lg` | 18px | screen titles |
| `text-xl` | 22px | hero and empty states |

These override Tailwind's defaults on purpose. A sixth size is a design decision — make it in
`tailwind.config.js`, don't smuggle it in as `text-[12px]`.

The person can scale all five at once, and only that way: Settings › Appearance › Text size stamps
`data-text-size` on `<html>`, which moves the rem root to 93.75% or 112.5%. Every size, space and
radius is in rem, so the interface scales together and the scale stays five sizes. The fonts work the
same way: Interface font and Code font stamp `data-font` / `data-font-code`, which swap the **value**
of `--font-sans` / `--font-mono` and nothing else. OpenDyslexic is served from `public/fonts`, never a
CDN; the code fonts are the computer's own and the row marks one it does not have. Each default stamps
no attribute, so someone who never opens the card sees the page as it was.

### Motion

| Token | Value | Use it for |
|---|---|---|
| `duration-1` | 120ms | hover, press — anything under the pointer |
| `duration-2` | 200ms | overlays entering, list items arriving |
| `duration-3` | 320ms | a column sliding into place |
| `duration-4` | 520ms | the ambient wash on first paint |
| `ease-out` | `cubic-bezier(.16,1,.3,1)` | **the house easing.** Fast start, long settle |
| `ease-in-out` | `cubic-bezier(.65,0,.35,1)` | something leaving and returning |
| `ease-spring` | `cubic-bezier(.34,1.56,.64,1)` | overshoots. Brand mark and send button only |

Every `transition` carries both a duration and an easing. An unstated transition inherits Tailwind's
implicit 150ms, and unstated timing is exactly how an app's rhythm drifts apart.

---

## Do / Don't

| Don't | Do | Why |
|---|---|---|
| `text-[13px]` | `text-sm` | the scale exists; using it is how it stays a scale |
| `border-white/5` | `border-hairline` | white-on-white is invisible in the light theme |
| `bg-white/[0.05]` | `bg-surface-2` | same |
| `hover:bg-white/5` | `hover:bg-surface-hover` | same |
| `text-[hsl(38_92%_62%)]` | `text-warn-foreground` | a literal can't follow the theme |
| `transition` | `transition duration-1 ease-out` | state the timing |
| `transition-[height]` | animate `transform` | height animation forces layout every frame |
| a new focus ring | `focusRing` from `ui/focus.ts` | one definition, one place to fix it |

---

## Motion spec

### The one choreography

The launch sequence, ~900ms, fires **once on cold start**. Ambient glow → brand mark → rail →
each rail icon on a 40ms stagger → context column from the left → main column from below →
inspector column from the right → and at 560ms one accent hairline draws itself left-to-right under
the header and settles. Everything converges inward from three directions, then one line lands.

It is one gesture, it is cheap (a 1px element scaling on the compositor), and it reads as *the app
arriving*. It never replays on re-render or on HMR.

### Everything else

- **View change**: incoming only. Fade + 6px rise, `duration-2 ease-out`. No directional slides —
  with five destinations there is no spatial model to reinforce, and slides make navigation feel
  slower than it is.
- **Hover / press**: `duration-1`.
- **Overlays**: enter `scale(.97)→1` + fade at `duration-2`; exit at `duration-1`.
- **Streaming**: the caret blinks on `steps(1)` — an eased sine reads as a heartbeat, not a cursor.
  Tool events arrive on a 40ms stagger. That is the only per-event animation in the app, and it is
  the one that sells "the agent is working".
- **Never** animate the transcript scroll per token. Write `scrollTop` inside a rAF instead; a
  smooth-scroll restarted 30×/second never completes and fights the user who scrolled up.

### Reduced motion — the contract

Honour **both** `@media (prefers-reduced-motion: reduce)` and `[data-motion="reduced"]` (the user
override: Settings › Appearance › Motion, System / Full / Reduced; on Windows the OS flag is often off
while the person still wants calm UI). The appearance provider behind that row (`lib/appearance.tsx`)
calls `applyMotion`, and the gate fails if nothing outside `lib/theme.ts` does: for a while this
paragraph promised a row that did not exist, and the function had no caller at all.

**Collapse durations to 1ms. Never `animation: none`.** That is the obvious move and it is a bug
factory: any element whose keyframes start at `opacity: 0` stays invisible forever, and
`animationend` never fires, so presence hooks strand mounted children and the launch class never
clears.

Reduced ≠ nothing. The launch sequence gets a designed variant: one 140ms fade of the whole shell,
no translation, no stagger. Still an arrival — just not a journey. Ambient loops go static.

**The gate enforces this**: a `@keyframes` with no reduced-motion answer fails the suite.

---

## Accessibility contract

- Every interactive element has a **visible focus ring**. Icon-only buttons carry a real label, not
  just `title=` (which is keyboard-inaccessible).
- Every async surface has a status region. In a streaming app, announce **state transitions**
  ("Thinking", "Using web_search", "Response ready") — never wrap the streaming text itself in a
  live region, or a screen reader re-reads the growing string on every token.
- Changing view moves focus to the new region's heading. Otherwise a keyboard user tabs from the top
  of the app every single time.
- Landmarks (`nav` / `main` / `aside` / `header` / `section`) are already correct. Don't regress them.

---

## Component inventory

Before building a primitive, check `src/components/ui/` — the Switch was independently invented
twice before this file existed.

| Component | File | Notes |
|---|---|---|
| `Button` | `ui/button.tsx` | |
| `Screen`, `Panel`, `Badge`, `Spinner`, `EmptyState` | `ui/panel.tsx` | |
| `ErrorState` | `ui/async.tsx` | |
| `Switch` | `ui/switch.tsx` | **requires a `label`** — an unnamed switch announces as "switch, on" |
| `Tabs`, `TabPanel` | `ui/tabs.tsx` | roving tabindex; the strip is one tab stop |
| `Dialog` | `ui/dialog.tsx` | Radix. Restores focus to whatever opened it, not to `<body>` |
| `Tooltip`, `TooltipProvider` | `ui/tooltip.tsx` | Radix. Use instead of `title=`, which keyboard users never see |
| `Select` | `ui/select.tsx` | Radix. For long lists or options needing a hint line — a native `<select>` is fine otherwise |
| `ToastProvider`, `useToast` | `ui/toast.tsx` | transient only; anything needing a decision belongs in its own surface |
| `focusRing` | `ui/focus.ts` | the one focus-ring definition |
| `BrandMark` | `BrandMark.tsx` | |

**Dependencies:** four headless Radix packages (dialog, tooltip, select, dropdown-menu), and `@dnd-kit` since
the dynamic screen's phase 4. Tabs, Switch and Toast are hand-built — each is well under a hundred lines, and a dependency
should buy something harder than that. Radix earns its place on the parts that are genuinely hard to
get right: focus traps, collision detection, typeahead.

---

## Layout contract

The shell provides slots; a screen fills the ones it needs.

| View | context (left) | inspector (right) |
|---|---|---|
| Chat | sessions | activity + fusion |
| Work | run receipts | live run stream |
| Code | file tree | run panel / diff |
| Knowledge, Automation | — | — |

A screen that opts out of the shell entirely is what made this app feel like a menu of features
rather than one workspace. Opt out only with a reason.

### Dynamic layout

The owner approved, on 2026-09-29, a screen where anything can be minimised, maximised, closed,
dragged, resized and brought back. It lands in phases; phase 0 is the model it all draws from:
`lib/layout/model.ts` (one serialisable value and one pure `applyLayout`), `lib/layout/store.ts`
(local storage, one key per screen, moving to the server in phase 6) and `lib/layout/context.tsx`.

**Five things never disappear.** Each can shrink; none can go without a trace, because hiding it
would leave the person not knowing what the agent is doing, or unable to stop it.

| | May | What stays when it shrinks |
|---|---|---|
| Approval card | minimise | one line, and the approvals chip in the status bar; a new approval reopens it |
| Stop | nothing | always in the composer and in the status bar |
| Status bar | compact (a later phase) | the state and the way back to anything hidden |
| Spend and limit warnings | minimise | a count on the turn |
| A failed turn's error | minimise | the error line, without the detail |

These are enforced in `applyLayout`, not in the buttons: a refused action returns the same object,
and Stop and the status bar are not panels at all, so no action can reach them. Tested in
`model.test.ts`, each rule sabotaged once and watched to fail.

**Every hidden thing has a way back that needs no remembered shortcut.** The status bar's hidden
tray (`shell/HiddenTray.tsx`) lists it with "Show". It renders nothing while nothing is hidden, the
same rule `PendingApprovals` follows about an indicator at zero, so "Restore default layout" lives
in the command palette, where it is always reachable.

**Phase 1: side regions.** The rail, the conversation list and the right panel hide from a button in
their own header, from `⌘B` / `⌘⌥B` and from the palette. A hidden region leaves a tab on its edge
(`shell/RegionToggle.tsx`), and hiding moves focus onto that tab. A region that comes back slides in
from its edge at `duration-3`, only on coming back; its parent owns the animation, because the parent
stays mounted and can tell "shown again" from "the screen just opened".

**Phase 2: widths.** The conversation list, the right panel and the file viewer take their widths
from the layout, dragged on `shell/Splitter.tsx` (the WAI-ARIA window splitter: arrows move 16px,
Home and a double click restore the starting width). One drag is one step to undo.

**Phase 3: cards.** Every card of the conversation carries the same three controls in its corner
(`code/CardChrome.tsx`), rather than a new title bar that would repeat the heading each card already
has: minimise to one line, minimise the whole kind (kept in the layout), close. Closing is for this
screen only, with an Undo (the toast's one action) and a "hidden in this turn" chip where the card was.
The approval card, spend warnings and a failed turn's error keep a disabled close button whose tooltip
says why. Two palette commands set every kind at once: "Cards: compact" minimises the tool list, the
receipt and the browser, "Cards: detailed" opens everything. Neither ever minimises those three
(`cardPresetActions` in the model checks each kind), and each is one step to undo. The conversation's
width (narrow / medium / wide, mapped to `max-w-2xl` / `3xl` / `5xl`) is part of the layout too, set
from Settings › Appearance or the palette, so it travels to the server with the rest.

**Phase 4: docks.** The right panel's sections are panels (`shell/Dock.tsx`) that move between the
right panel, the left sidebar and a bottom dock that exists only while it holds one. Each has a drag
handle, a "Move to" menu, minimise and close. The agent's state line is not a panel. The composer's
settings minimise to one line of chips; the posture note beside them never does.

**Phase 5: maximise and focus.** One panel at a time fills the main area (`shell/Maximize.tsx`) and
Escape always restores it. Focus mode (the status bar's focus button) remembers the layout it replaced
and returns to it exactly. "Review", "monitor" and the person's own saved layout are palette commands.

**Phase 6: kept by the server.** The layout also lives in `CHIMERA_HOME/ui_layout.json` through
`/api/ui/layout` (`lib/layout/sync.tsx`): local goes up the first time, the server's is applied after
that, a change made before it answers wins, and no server is not an error. A screen's own left sidebar
(the editor's, in the shell's context slot) follows the left region.

**Phase 7: a panel in its own window.** A dock panel opens in a window from its move menu
(`lib/float/host.tsx`, `components/shell/FloatWindow.tsx`). The window is the same origin asked by
`?float=` to draw one panel, with no layout of its own; the agent's state crosses over a
`BroadcastChannel`. Floating is state of the run, never part of the layout, so nothing stored can point
at a window that is gone. The tray lists a floating panel with "Bring back", beside what is hidden.

**Dependencies.** `@dnd-kit` (core, sortable, utilities) joined the four Radix packages in phase 4, for
the reason given above: it buys something harder than a hundred lines, the keyboard half of dragging,
with every step announced. `react-resizable-panels` was approved for phase 2 and **not adopted**: it sizes sibling
panels inside one group, while here the right panel lives in the shell and the conversation list inside
the Code screen, and the layout already keeps the widths, their limits and their storage. The splitter
it would have bought is one small file.

### Information architecture

Five destinations, plus Settings pinned to the rail footer. Fifteen icons stopped being words and
became positions to memorise.

```
Chat · Work · Code · Knowledge · Automation          ⚙ Settings
        │      │        │            │                  ├ General
        │      │        │            │                  ├ Connections
     Run │      │   Memory        Schedule              │   ├ MCP servers
  Agents │      │   Profile          Tasks              │   └ Capabilities
                                     Skills             ├ Usage
                                                        └ Security
```

Three rules that decided the shape, worth reapplying to anything new:

1. **A screen that is empty for an installed user should not ship.** Maturity measures the Chimera
   project's own test coverage and needs a source checkout, so it is `import.meta.env.DEV` only.
   A permanently blank screen is a trust cost with no upside.
2. **A turn detail is not a place.** Fusion was a destination that rendered only when the
   *immediately preceding* chat turn had used fusion — you had to send a fused message, navigate
   away, and read it before the next message erased it. It is a section of the activity inspector
   now, and more discoverable for losing its icon.
3. **A read-only inspector is not a daily surface.** Tools had no action available at all and MCP's
   own empty state says the CLI is the source of truth. Both are Settings › Connections.

Adding a sixth rail icon is a real decision, not a small one. Ask first whether the thing is a
question a person actually has, or a feature you want them to notice.

### Keyboard

| | |
|---|---|
| `⌘K` / `Ctrl K` | command palette — every destination, every tab, every conversation by title |
| `⌘1`–`⌘5` | rail positions |
| `⌘N` | new chat |
| `⌘,` | settings |
| `⌘B` / `⌘⌥B` | hide or show the left sidebar / the right panel (by the physical B key) |
| `⌘⇧F` | focus mode on and off |
| `⌘⇧M` | maximise the panel that holds focus, or restore the maximised one |
| `⌘⇧A` | go to the approval waiting in the conversation |
| `Esc` | restore a maximised panel, from anywhere (a menu or dialog open first takes it) |

The palette is what makes a five-icon rail cost nothing in reach: the long tail lives there instead
of on screen.

**Every shortcut except `⌘K` is suppressed while the user is typing.** `⌘N` inside the composer
would discard a half-written message, and a shortcut that destroys work is worse than no shortcut.
`⌘K` is the deliberate exception — a palette exists to be reachable without moving your hands, and
it opens *over* the field rather than acting on it.
