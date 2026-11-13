/**
 * `setInterval` that sleeps while the window is hidden.
 *
 * A clock on screen has nothing to redraw when nobody can see it, and a timer that keeps firing in a
 * minimised window still wakes the page and re-renders its component. Every callback here is a
 * "recompute from the wall clock" tick, so nothing is lost by pausing: on the way back to visible
 * it runs once at once — the figure on screen is current the moment the window is — and resumes.
 *
 * Returns the cancel function, to be handed back from an effect.
 */
export function setVisibleInterval(tick: () => void, ms: number): () => void {
  let timer: ReturnType<typeof setInterval> | null = null;
  const stop = () => {
    if (timer !== null) clearInterval(timer);
    timer = null;
  };
  const start = () => {
    if (timer === null) timer = setInterval(tick, ms);
  };
  const onVisibility = () => {
    if (document.hidden) {
      stop();
    } else if (timer === null) {
      tick();
      start();
    }
  };
  if (!document.hidden) start();
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    stop();
    document.removeEventListener("visibilitychange", onVisibility);
  };
}
