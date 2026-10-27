import { useEffect, useState } from "react";

/** Whether a CSS media query matches now, and follows the window as it is resized. Without
 *  `matchMedia` (jsdom, very old webviews) it answers false: the wide layout is the default. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => readQuery(query));
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const list = window.matchMedia(query);
    const update = () => setMatches(list.matches);
    update();
    list.addEventListener("change", update);
    return () => list.removeEventListener("change", update);
  }, [query]);
  return matches;
}

function readQuery(query: string): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
}
