import { useCallback, useEffect, useState } from "react";

import { VIEWS, type View } from "@/components/IconRail";

/**
 * Where you are, in the URL.
 *
 * The app navigated with `useState<View>`, which works until something outside the switch needs to
 * point at a place: "open this file at line 42" from a diagnostic, a search hit, or a tool event.
 * With no address for a screen, every one of those becomes a callback threaded through the shell.
 * The editor makes it unavoidable — open tabs and the active file are exactly the state that has to
 * survive a reload and answer the back button, and re-implementing history by hand is how that ends.
 *
 * **Hash, not path.** A path router needs the server to send index.html for unknown URLs. That works
 * here (`app.py` has an SPA fallback) but the app also runs from a Tauri webview origin and against
 * a REMOTE Chimera (`src/lib/server.ts`), and each is a different chance for the fallback to be
 * wrong. The hash never reaches a server, so there is no configuration to get wrong.
 *
 * **Hand-rolled, not react-router.** Six destinations and one params bag. The library is 12 KB and a
 * whole mental model for something the platform already does with `hashchange`.
 */

/** The screen shown when the URL says nothing, or says something that is not a screen. */
export const DEFAULT_VIEW: View = "code";

export interface Route {
  readonly view: View;
  /** Everything after `?` in the hash. Empty for a bare `#/code`. */
  readonly params: URLSearchParams;
}

/**
 * `maturity` reports this project's own test coverage and needs a source checkout; the rail hides it
 * outside development and `App` refuses to render it. Without this it would still PARSE in a shipped
 * build, so a link to `#/maturity` would land on a blank screen — worse than the URL simply not
 * working, because a blank screen looks like a broken app rather than a stale link.
 */
const DEV_ONLY: ReadonlySet<string> = new Set(["maturity"]);

function isView(value: string, allowDev: boolean): value is View {
  if (DEV_ONLY.has(value) && !allowDev) return false;
  return (VIEWS as readonly string[]).includes(value);
}

/**
 * Read a hash into a route. Never throws and never returns something that is not a screen — a stale
 * or hand-edited URL lands on the default rather than rendering nothing.
 */
export function parseHash(hash: string, allowDev: boolean = import.meta.env.DEV): Route {
  const raw = hash.replace(/^#\/?/, "");
  const [path = "", query = ""] = raw.split("?", 2);
  const view = decodeURIComponent(path);
  return {
    view: isView(view, allowDev) ? view : DEFAULT_VIEW,
    params: new URLSearchParams(query),
  };
}

/** The inverse. Empty params produce a bare `#/code`, so the common URL stays readable. */
export function formatHash(view: View, params?: Record<string, string>): string {
  const query = new URLSearchParams(params ?? {}).toString();
  return query ? `#/${view}?${query}` : `#/${view}`;
}

export interface Router {
  readonly route: Route;
  /** Convenience: `route.view`, which is what almost every caller wants. */
  readonly view: View;
  /** Push a new location. Back/forward then work because the browser owns the history. */
  navigate: (view: View, params?: Record<string, string>) => void;
  /** Change params without leaving the screen — replaces rather than pushes, so a cursor move or a
   *  tab switch does not fill the back button with steps nobody wants to retrace. */
  setParams: (params: Record<string, string>) => void;
}

export function useRoute(): Router {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));

  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    // The hash can change between the first render and this effect attaching — a deep link that the
    // shell rewrites, or a second tab restoring state. Re-read once so the two cannot disagree.
    onChange();
    return () => window.removeEventListener("hashchange", onChange);
  }, []);

  const navigate = useCallback((view: View, params?: Record<string, string>) => {
    const next = formatHash(view, params);
    window.location.hash = next;
    // Set the state too, rather than waiting to hear about our own navigation.
    //
    // `hashchange` is ASYNCHRONOUS — the browser queues it, so between the click and the event the
    // screen would still be the old one. Rare enough to look like a flicker and impossible to
    // reproduce on demand, which is the worst kind of bug to be handed. Assigning an unchanged hash
    // also fires no event at all, so a repeat click would have gone nowhere.
    //
    // The listener stays: it is what makes back/forward and an externally-edited URL work, and
    // arriving at the same value twice costs nothing.
    setRoute(parseHash(next));
  }, []);

  const setParams = useCallback((params: Record<string, string>) => {
    const current = parseHash(window.location.hash);
    const next = formatHash(current.view, params);
    const url = `${window.location.pathname}${window.location.search}${next}`;
    window.history.replaceState(null, "", url);
    setRoute(parseHash(next));
  }, []);

  return { route, view: route.view, navigate, setParams };
}
