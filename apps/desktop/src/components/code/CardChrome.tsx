import { useCallback, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronsDownUp, ChevronsUpDown, Minus, X } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useOptionalToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { canCloseCard, type CardKind } from "@/lib/layout/model";
import { cn } from "@/lib/utils";

/**
 * The controls every card of the conversation carries: phase 3 of the dynamic screen.
 *
 * A card can be minimised to one line, closed (only that card, in that turn), or have its whole KIND
 * minimised from now on ("always minimise the tool list"). Three rules decide the rest:
 *
 * - **What is closed is only for this screen's session.** It is not stored: reopening a conversation
 *   shows its cards. What IS stored is the per-kind preference, in the layout.
 * - **Approval, spend warnings and a failed turn's error minimise and never close.** The layout says
 *   so (`canCloseCard`); the close button is there, disabled, with the reason as its tooltip, so the
 *   person learns why rather than wondering where the button went.
 * - **A new approval always opens,** whatever the preference, because it is a decision waiting.
 *
 * The controls sit in the card's corner rather than in a new title bar: every card already has its own
 * heading, and a second one above it would say everything twice.
 */
export type CardMode = "open" | "minimized" | "closed";

/** An instance's key: which turn, which kind, which one. The kind is part of it so a per-kind change
 *  can find the instances it overrides. */
export function cardId(turn: number, kind: CardKind, n = 0): string {
  return `${turn}:${kind}:${n}`;
}

export interface CardModes {
  mode: (id: string, kind: CardKind) => CardMode;
  set: (id: string, kind: CardKind, mode: CardMode) => void;
  setKind: (kind: CardKind, mode: "open" | "minimized") => void;
  closedInTurn: (turn: number) => number;
  showTurn: (turn: number) => void;
}

/** The cards' state for one conversation screen. Remounting the screen (another conversation) resets
 *  what was closed, which is the point: closing is a tidy-up of this view, not a deletion. */
export function useCardModes(): CardModes {
  const t = useT();
  const toast = useOptionalToast();
  const { layout, dispatch } = useLayout();
  const [overrides, setOverrides] = useState<Record<string, CardMode>>({});

  const mode = useCallback(
    (id: string, kind: CardKind): CardMode =>
      overrides[id] ?? (kind === "approval" ? "open" : layout.cards[kind]),
    [overrides, layout.cards],
  );

  const set = useCallback(
    (id: string, kind: CardKind, next: CardMode) => {
      if (next === "closed" && !canCloseCard(kind)) return;
      setOverrides((prev) => ({ ...prev, [id]: next }));
      if (next === "closed") {
        const name = t(`layout.card.name.${kind}`);
        toast(t("layout.card.closed", { name }), "info", {
          label: t("layout.undo"),
          run: () => setOverrides((prev) => ({ ...prev, [id]: "open" })),
        });
      }
    },
    [t, toast],
  );

  const setKind = useCallback(
    (kind: CardKind, next: "open" | "minimized") => {
      dispatch({ type: "card-pref", kind, mode: next });
      // The new preference speaks for every card of the kind; a per-card choice made before it would
      // otherwise keep one card open under "always minimise". Closed ones stay closed.
      setOverrides((prev) =>
        Object.fromEntries(
          Object.entries(prev).filter(([id, m]) => m === "closed" || id.split(":")[1] !== kind),
        ),
      );
    },
    [dispatch],
  );

  const closedInTurn = useCallback(
    (turn: number) =>
      Object.entries(overrides).filter(([id, m]) => m === "closed" && id.startsWith(`${turn}:`)).length,
    [overrides],
  );

  const showTurn = useCallback((turn: number) => {
    setOverrides((prev) =>
      Object.fromEntries(
        Object.entries(prev).map(([id, m]) => [id, id.startsWith(`${turn}:`) && m === "closed" ? "open" : m]),
      ),
    );
  }, []);

  return useMemo(
    () => ({ mode, set, setKind, closedInTurn, showTurn }),
    [mode, set, setKind, closedInTurn, showTurn],
  );
}

const iconButton = cn(
  "rounded-sm p-0.5 text-muted-foreground transition-colors duration-1 ease-out",
  "hover:bg-surface-hover hover:text-foreground",
  focusRing,
);

export function CardChrome({
  id,
  kind,
  cards,
  summary,
  children,
}: {
  id: string;
  kind: CardKind;
  cards: CardModes;
  /** A few words for the minimised line: "3 tools", "2 warnings". The kind's name when absent. */
  summary?: string;
  children: ReactNode;
}) {
  const t = useT();
  const { layout } = useLayout();
  const mode = cards.mode(id, kind);
  if (mode === "closed") return null;

  const name = t(`layout.card.name.${kind}`);
  const minimized = mode === "minimized";
  const kindMinimized = layout.cards[kind] === "minimized";
  const closable = canCloseCard(kind);

  const controls = (
    <div className="flex items-center gap-0.5">
      <Tooltip label={t(minimized ? "layout.card.expand" : "layout.card.minimize", { name })}>
        <button
          type="button"
          aria-label={t(minimized ? "layout.card.expand" : "layout.card.minimize", { name })}
          aria-expanded={!minimized}
          onClick={() => cards.set(id, kind, minimized ? "open" : "minimized")}
          className={iconButton}
        >
          {minimized ? <ChevronDown className="h-3.5 w-3.5" /> : <Minus className="h-3.5 w-3.5" />}
        </button>
      </Tooltip>
      {kind !== "approval" && (
        <Tooltip label={t(kindMinimized ? "layout.card.expandKind" : "layout.card.minimizeKind", { name })}>
          <button
            type="button"
            aria-label={t(kindMinimized ? "layout.card.expandKind" : "layout.card.minimizeKind", { name })}
            aria-pressed={kindMinimized}
            onClick={() => cards.setKind(kind, kindMinimized ? "open" : "minimized")}
            className={iconButton}
          >
            {kindMinimized ? <ChevronsUpDown className="h-3.5 w-3.5" /> : <ChevronsDownUp className="h-3.5 w-3.5" />}
          </button>
        </Tooltip>
      )}
      <Tooltip label={closable ? t("layout.card.close", { name }) : t(`layout.card.why.${kind}`)}>
        <button
          type="button"
          aria-label={t("layout.card.close", { name })}
          // Disabled, and still there: the reason is the tooltip, and a button that vanished would
          // leave the person hunting for it.
          aria-disabled={!closable}
          onClick={() => closable && cards.set(id, kind, "closed")}
          className={cn(iconButton, !closable && "cursor-not-allowed opacity-40 hover:bg-transparent")}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </Tooltip>
    </div>
  );

  if (minimized) {
    return (
      <div
        data-card={kind}
        className="flex items-center gap-2 rounded-chip border border-hairline px-2 py-1 text-xs text-muted-foreground"
      >
        <span className="min-w-0 flex-1 truncate">{summary ?? name}</span>
        {controls}
      </div>
    );
  }
  return (
    <div data-card={kind} className="group/card relative">
      {children}
      {/* In the corner, quiet until the card is looked at or reached by keyboard. */}
      <div
        className={cn(
          "absolute right-1 top-1 rounded-sm bg-card/80 opacity-60 transition-opacity duration-1 ease-out",
          "group-hover/card:opacity-100 focus-within:opacity-100",
        )}
      >
        {controls}
      </div>
    </div>
  );
}
