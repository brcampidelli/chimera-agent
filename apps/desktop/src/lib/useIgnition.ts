import { useEffect, useState } from "react";

/** Total run of the sequence in motion.css: last beat starts at 560ms and runs 420ms. */
const IGNITION_MS = 980;

/**
 * Marks the session in which the launch sequence has already played. `sessionStorage`, not
 * `localStorage`, on purpose: it should play once per app launch, and it should NOT replay on every
 * hot reload while someone is working on the UI — which in dev is dozens of times an hour.
 */
const PLAYED_KEY = "chimera.ignited";

function alreadyPlayed(): boolean {
  try {
    return sessionStorage.getItem(PLAYED_KEY) === "1";
  } catch {
    return false;
  }
}

function markPlayed(): void {
  try {
    sessionStorage.setItem(PLAYED_KEY, "1");
  } catch {
    // Storage unavailable — the worst case is the sequence replaying, which is not a failure.
  }
}

/**
 * Whether to run the launch choreography on this mount.
 *
 * The app deliberately has exactly one piece of choreography, and it is here. Scattering motion
 * across an interface is what makes it feel busy and cheap; concentrating it is what lets one
 * moment land. The trade is that this moment must not become a tax — by the fiftieth cold start,
 * nobody wants a second of ceremony before their work — hence `enabled`, which is the switch for
 * trimming it later without unpicking the CSS.
 *
 * Returns `ignite` as a class flag. It is cleared once the sequence ends, which also drops the
 * `will-change` promotions so they do not linger as extra compositor layers for the whole session.
 */
export function useIgnition(enabled = true): boolean {
  const [ignite, setIgnite] = useState(() => enabled && !alreadyPlayed());

  useEffect(() => {
    if (!ignite) return;
    markPlayed();
    // A timer rather than `animationend`: the sequence is many elements with different delays, so
    // there is no single element whose end means "done" — and under reduced motion (durations
    // collapsed to 1ms) the events fire almost immediately anyway, which is the correct outcome.
    const timer = setTimeout(() => setIgnite(false), IGNITION_MS);
    return () => clearTimeout(timer);
  }, [ignite]);

  return ignite;
}
