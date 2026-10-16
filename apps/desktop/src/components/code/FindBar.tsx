import { useEffect, useRef, useState, type RefObject } from "react";
import { ChevronDown, ChevronUp, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { matchesShortcut, shortcutLabel } from "@/lib/hotkeys";
import { clearMatches, findRanges, paintMatches, revealMatch } from "@/lib/findInConversation";
import { useNum, useT } from "@/lib/i18n";

/**
 * The find bar over the conversation (⌘F).
 *
 * `scope` is the conversation's column; the bar searches only the transcript inside it (its
 * `role="log"`), so a match is never the composer's own draft or the bar's query. The matches are recomputed when the transcript changes — a turn streaming in
 * while the bar is open adds its occurrences without the query being typed again — and the current
 * one keeps its place in the count when it can.
 *
 * `step` is how the screen's F3 / ⇧F3 reach the bar when focus is not in it: it bumps on every
 * press, and the sign says which way.
 */
export function FindBar({
  scope,
  onClose,
  focusTick,
  step,
}: {
  scope: RefObject<HTMLElement | null>;
  onClose: () => void;
  /** Bumped by ⌘F while the bar is already open: focus the field again and select what is in it. */
  focusTick: number;
  step: { n: number; dir: 1 | -1 };
}) {
  const t = useT();
  const num = useNum();
  const input = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [ranges, setRanges] = useState<Range[]>([]);
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, [focusTick]);

  // Recount on every change to the query or to the transcript under it.
  useEffect(() => {
    const el = scope.current?.querySelector<HTMLElement>('[role="log"]');
    if (!el) return;
    const recount = () => setRanges(findRanges(el, query));
    recount();
    const watcher = new MutationObserver(recount);
    watcher.observe(el, { subtree: true, childList: true, characterData: true });
    return () => watcher.disconnect();
  }, [scope, query]);

  // A new query starts at the first match; a recount under the same query keeps the place.
  useEffect(() => setCurrent(0), [query]);
  const index = ranges.length === 0 ? 0 : Math.min(current, ranges.length - 1);

  useEffect(() => {
    paintMatches(ranges, index);
  }, [ranges, index]);
  // Closing clears the paint: nothing is left highlighted in a transcript with no bar over it.
  useEffect(() => clearMatches, []);

  function move(dir: 1 | -1) {
    if (ranges.length === 0) return;
    const next = (index + dir + ranges.length) % ranges.length;
    setCurrent(next);
    revealMatch(ranges[next]);
  }

  const lastStep = useRef(step.n);
  useEffect(() => {
    if (step.n === lastStep.current) return;
    lastStep.current = step.n;
    move(step.dir);
    // `move` reads the latest ranges through this render; re-running on its identity would step twice.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step]);

  const label = t("find.placeholder");
  const status = query.trim()
    ? ranges.length === 0
      ? t("find.none")
      : t("find.count", { n: num(index + 1), total: num(ranges.length) })
    : "";

  return (
    <div
      role="search"
      aria-label={label}
      className="flex items-center gap-1 border-b border-hairline px-3 py-1.5"
      // Escape from anywhere in the bar — the field or one of its buttons — closes it.
      onKeyDown={(e) => {
        if (!matchesShortcut(e, "findClose")) return;
        e.preventDefault();
        onClose();
      }}
    >
      <input
        ref={input}
        type="search"
        aria-label={label}
        placeholder={label}
        className="field h-7 min-w-0 flex-1 px-2 text-sm"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          // The field answers its own keys: the screen's shortcuts stand down while you type.
          if (matchesShortcut(e, "find")) {
            // ⌘F again from inside the field: select the query, as a browser's find bar does,
            // instead of handing the key to the WebView's own find.
            e.preventDefault();
            e.currentTarget.select();
          } else if (e.key === "Enter" || matchesShortcut(e, "findNext") || matchesShortcut(e, "findPrevious")) {
            e.preventDefault();
            move(e.shiftKey ? -1 : 1);
          }
        }}
      />
      <span role="status" className="shrink-0 px-1 text-xs text-muted-foreground">
        {status}
      </span>
      <Button
        size="sm"
        variant="ghost"
        aria-label={`${t("hotkeys.findPrevious")} (${shortcutLabel("findPrevious") ?? ""})`}
        disabled={ranges.length === 0}
        onClick={() => move(-1)}
      >
        <ChevronUp className="h-4 w-4" />
      </Button>
      <Button
        size="sm"
        variant="ghost"
        aria-label={`${t("hotkeys.findNext")} (${shortcutLabel("findNext") ?? ""})`}
        disabled={ranges.length === 0}
        onClick={() => move(1)}
      >
        <ChevronDown className="h-4 w-4" />
      </Button>
      <Button size="sm" variant="ghost" aria-label={t("hotkeys.findClose")} onClick={onClose}>
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}
