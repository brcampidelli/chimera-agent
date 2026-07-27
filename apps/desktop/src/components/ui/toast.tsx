import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { AlertTriangle, Check, Info, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";
import { usePresence } from "@/lib/usePresence";

type Tone = "info" | "ok" | "bad";

interface Toast {
  id: number;
  message: string;
  tone: Tone;
}

const ToastContext = createContext<((message: string, tone?: Tone) => void) | null>(null);

/**
 * Transient feedback.
 *
 * Hand-built: Radix's Toast is the heaviest of the family and most of its weight is the swipe
 * gestures and the hotkey to jump to the region — neither of which a desktop app with a persistent
 * status bar needs. What it does need is an announcement a screen reader hears, and that is one
 * `role="status"` region.
 *
 * Deliberately not for errors that need a decision. A toast disappears; anything the user must act
 * on belongs in the surface that owns it.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (message: string, tone: Tone = "info") => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, message, tone }]);
      // Errors stay longer: they are read more slowly and more carefully.
      setTimeout(() => dismiss(id), tone === "bad" ? 7000 : 4000);
    },
    [dismiss],
  );

  const value = useMemo(() => show, [show]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* aria-live on the CONTAINER, not on each toast: a live region has to exist in the DOM before
          the content arrives, or the change goes unannounced. */}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 flex-col items-center gap-2"
      >
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const ICONS: Record<Tone, typeof Info> = { info: Info, ok: Check, bad: AlertTriangle };
const TONE_CLASS: Record<Tone, string> = {
  info: "text-muted-foreground",
  ok: "text-ok",
  bad: "text-bad",
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  // Mounted until its exit animation finishes — a toast that vanishes mid-fade reads as a glitch.
  const { mounted, state, onAnimationEnd } = usePresence(true);
  const Icon = ICONS[toast.tone];
  if (!mounted) return null;
  return (
    <div
      data-state={state}
      onAnimationEnd={onAnimationEnd}
      className="overlay floating pointer-events-auto flex items-center gap-2.5 px-3.5 py-2.5 text-sm"
    >
      <Icon className={cn("h-4 w-4 shrink-0", TONE_CLASS[toast.tone])} />
      <span className="max-w-sm">{toast.message}</span>
      <button
        type="button"
        onClick={onDismiss}
        aria-label="Dismiss"
        className={cn(
          "-mr-1 rounded p-0.5 text-muted-foreground",
          "transition-colors duration-1 ease-out hover:text-foreground",
          focusRing,
        )}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/** Show a toast. Throws outside the provider rather than silently doing nothing. */
export function useToast(): (message: string, tone?: Tone) => void {
  const show = useContext(ToastContext);
  if (!show) throw new Error("useToast must be used inside <ToastProvider>");
  return show;
}
