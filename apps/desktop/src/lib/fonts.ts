/**
 * Whether a font can actually be drawn here — so a font row never offers a choice that does nothing.
 *
 * Code fonts are not shipped with the app; they are used when the computer has them. Picking one that
 * is missing would leave the page unchanged after a control said it changed, so the row marks it.
 *
 * Detection is by measurement, the only check a webview offers for an installed font: the same text
 * is measured in a generic family and in "the font, else that generic". If no generic changes width,
 * the font fell back, so it is not here. `document.fonts.check` is no help for this: it answers "true"
 * for a family it has nothing to load for, which is every installed font and every missing one alike.
 *
 * `null` means "cannot tell" (no canvas, as in tests or a locked-down webview). The caller treats that
 * as available — not knowing is no reason to take a choice away.
 */
const SAMPLE = "mmmmmmmmmmlli1WQ@#0O";
const GENERICS = ["monospace", "serif", "sans-serif"] as const;

export function fontInstalled(family: string): boolean | null {
  try {
    const ctx = document.createElement("canvas").getContext("2d");
    if (!ctx) return null;
    for (const generic of GENERICS) {
      ctx.font = `72px ${generic}`;
      const fallback = ctx.measureText(SAMPLE).width;
      ctx.font = `72px "${family}", ${generic}`;
      if (ctx.measureText(SAMPLE).width !== fallback) return true;
    }
    return false;
  } catch {
    return null;
  }
}

/** True when any of the names is here, false when none is, null when it cannot be told. */
export function anyInstalled(...families: string[]): boolean | null {
  const answers = families.map(fontInstalled);
  if (answers.includes(true)) return true;
  return answers.includes(null) ? null : false;
}

/**
 * Whether a font the app ships in its own files loads. It is declared by `@font-face`, so it is not
 * "installed" until asked for; loading it first is what makes the answer real. A file that is missing
 * rejects, or resolves with no face, and both read as unavailable.
 */
export async function bundledFontLoads(family: string): Promise<boolean | null> {
  const fonts = typeof document !== "undefined" ? document.fonts : undefined;
  if (!fonts || typeof fonts.load !== "function") return null;
  try {
    const faces = await fonts.load(`16px "${family}"`);
    return faces.length > 0;
  } catch {
    return false;
  }
}
