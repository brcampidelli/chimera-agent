import { useEffect, useState } from "react";

import { useT, type TFunc } from "@/lib/i18n";

/** Elapsed seconds as the two largest translated units — "1 h 5 min", "2 min 0 s", "7 s".
 *
 *  Composed from the unit keys rather than from one sentence with three slots, so no language is
 *  handed an English word order; each key places its own number. Floors, never rounds: a timer that
 *  shows "2 s" one tick after "1 s" is reading a clock, one that jumps from "1 s" to "3 s" is not. */
export function formatElapsed(seconds: number, t: TFunc): string {
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  if (h > 0) return `${t("code.chat.duration.h", { n: h })} ${t("code.chat.duration.min", { n: m })}`;
  if (m > 0) return `${t("code.chat.duration.min", { n: m })} ${t("code.chat.duration.s", { n: sec })}`;
  return t("code.chat.duration.s", { n: sec });
}

/** A running clock from `startedAt` (ms), or a stopped one showing `finalSeconds`.
 *
 *  It re-reads `Date.now()` against the start on every tick instead of adding one per tick: an
 *  interval in a background window is throttled to once a minute, and a counter of ticks would
 *  come back to a turn that had run ten minutes reading "1 min". The text changes at most once a
 *  second and nothing else moves, so reduced motion has nothing to take away. */
export function Elapsed({
  startedAt,
  finalSeconds,
  testId,
}: {
  startedAt?: number;
  finalSeconds?: number;
  testId: string;
}) {
  const t = useT();
  const running = finalSeconds === undefined && startedAt !== undefined;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  const seconds = finalSeconds ?? (startedAt === undefined ? 0 : (now - startedAt) / 1000);
  return (
    <span className="tabular-nums text-muted-foreground" data-testid={testId}>
      {formatElapsed(seconds, t)}
    </span>
  );
}
