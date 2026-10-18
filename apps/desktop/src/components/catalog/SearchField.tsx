import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Search } from "lucide-react";

import { isTyping } from "@/lib/hotkeys";

/**
 * The catalog's search box, with `/` to reach it.
 *
 * `/` is suppressed while the person is typing anywhere — the app's rule for every shortcut but ⌘K
 * and dictation, because a shortcut that eats a keystroke from a draft destroys work. No ⌘F: inside
 * the webview it belongs to the page's own find.
 *
 * Escape works in two steps: the first clears the text, the second leaves the field. One step that
 * did both would throw away a query someone only wanted to leave.
 */
export function SearchField({
  value,
  onChange,
  label,
  className,
}: {
  value: string;
  onChange: (next: string) => void;
  /** Also the placeholder: "Search plugins…". */
  label: string;
  className?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTyping(e.target)) return;
      e.preventDefault();
      input.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key !== "Escape") return;
    // Ours either way: the app's global Escape (restore a maximised panel) must not also fire.
    e.preventDefault();
    e.stopPropagation();
    if (value) onChange("");
    else input.current?.blur();
  }

  return (
    <label className={className ?? "field flex h-8 min-w-0 flex-1 items-center gap-2 px-2"}>
      <Search aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
      <input
        ref={input}
        type="search"
        aria-label={label}
        placeholder={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        className="min-w-0 flex-1 bg-transparent text-sm outline-hidden placeholder:text-muted-foreground"
      />
      {!focused && !value && (
        <kbd aria-hidden className="rounded-md px-1 text-xs text-muted-foreground ring-1 ring-hairline">
          /
        </kbd>
      )}
    </label>
  );
}
