/**
 * The focus ring. One definition, so there is one place to change it.
 *
 * `focus-visible` rather than `focus`: a mouse click should not leave a ring behind, but a keyboard
 * user needs to know where they are. Before this existed the ring was defined exactly once, inside
 * the Button component — which meant the icon rail, the app's primary navigation, had no focus
 * indicator at all.
 *
 * A solid outline in the opaque `--ring`, not the `shadow-glow` halo it replaced. The halo was 20–28%
 * alpha over the page and measured 1.12–1.96:1 in every palette, while `outline-hidden` switched off
 * the browser's own ring (WCAG 1.4.11 asks 3:1). `--ring` is chosen per theme and accent so it clears
 * that against the page and every surface (`design/focus-ring-is-visible.test.ts` checks all 72
 * palettes), `--ring-width` is what high contrast thickens, and an outline — unlike a shadow —
 * survives Windows forced colors. The offset keeps it off the control's own fill.
 */
export const focusRing =
  "focus-visible:outline-(length:--ring-width) focus-visible:outline-solid focus-visible:outline-ring focus-visible:outline-offset-2 focus-visible:relative focus-visible:z-10";

/**
 * Ink for a drawing inside a focusable `group` (a category tile's illustration): it takes the
 * accent when its parent has keyboard focus, as it does under the pointer. Kept here so the
 * focus styles still have one home.
 */
export const groupFocusInk = "group-focus-visible:text-accent-ink";
