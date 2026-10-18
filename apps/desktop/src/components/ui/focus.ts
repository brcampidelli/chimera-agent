/**
 * The focus ring. One definition, so there is one place to change it.
 *
 * `focus-visible` rather than `focus`: a mouse click should not leave a ring behind, but a keyboard
 * user needs to know where they are. Before this existed the ring was defined exactly once, inside
 * the Button component — which meant the icon rail, the app's primary navigation, had no focus
 * indicator at all.
 */
export const focusRing =
  "focus-visible:outline-hidden focus-visible:shadow-glow focus-visible:relative focus-visible:z-10";

/**
 * Ink for a drawing inside a focusable `group` (a category tile's illustration): it takes the
 * accent when its parent has keyboard focus, as it does under the pointer. Kept here so the
 * focus styles still have one home.
 */
export const groupFocusInk = "group-focus-visible:text-accent-ink";
