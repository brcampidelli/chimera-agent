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
override in Settings › Appearance; on Windows the OS flag is often off while the person still wants
calm UI).

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

| Component | File |
|---|---|
| `Button` | `ui/button.tsx` |
| `Screen`, `Panel`, `Badge`, `Spinner`, `EmptyState` | `ui/panel.tsx` |
| `ErrorState` | `ui/async.tsx` |
| `BrandMark` | `BrandMark.tsx` |

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
