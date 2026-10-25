import { ArrowRight, Palette } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { CodeDesignFrame, CodeDesignSuggestion } from "@/lib/design/frames";
import { useNum, useT } from "@/lib/i18n";

/** Keys written out per action code, so the i18n reachability check sees each; an action this
 *  version does not know reads as a plain save. */
const ACTION_KEY: Record<string, string> = {
  create: "design.card.action.create",
  apply: "design.card.action.apply",
  propose: "design.card.action.propose",
  export_html: "design.card.action.export",
};

/** One card per design in a turn: a later frame for the same design replaces the earlier one. */
export function withDesign(list: readonly CodeDesignFrame[] | undefined, frame: CodeDesignFrame): CodeDesignFrame[] {
  return [...(list ?? []).filter((f) => f.document_id !== frame.document_id), frame];
}

/**
 * The message asked for a visual design. "Explicit" (it named Chimera Design) has already opened the
 * canvas beside the conversation, so the card says so and offers to bring it back; "suggest" only
 * offers. Either way the brief is the message itself, carried to the design panel.
 */
export function DesignSuggestionCard({
  suggestion,
  onOpen,
}: {
  suggestion: CodeDesignSuggestion;
  /** Absent where there is no viewer to open into (a window of its own). */
  onOpen?: () => void;
}) {
  const t = useT();
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-design-suggestion={suggestion.level}>
      <Palette className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
      <span className="min-w-0 flex-1 text-foreground">
        {t(suggestion.level === "explicit" ? "design.card.explicit" : "design.card.suggest")}
      </span>
      {onOpen ? (
        <Button type="button" size="sm" variant="outline" onClick={onOpen}>
          {t("design.card.open")}
        </Button>
      ) : null}
    </div>
  );
}

/** What the agent did to a design in this turn: its title, the revision it left, and a way in. */
export function DesignFrameCard({ frame, onOpen }: { frame: CodeDesignFrame; onOpen?: () => void }) {
  const t = useT();
  const num = useNum();
  const content = (
    <>
      <Palette className="h-4 w-4 shrink-0 text-accent" aria-hidden="true" />
      <span className="min-w-0 flex-1 text-left">
        <span className="block truncate font-medium text-foreground">{frame.title || t("design.pane.name")}</span>
        <span className="block text-xs text-muted-foreground">
          {t(ACTION_KEY[frame.action] ?? "design.card.action.apply")}
          {" · "}
          {t("design.card.revision", { n: num(frame.revision) })}
        </span>
      </span>
      <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </>
  );
  return onOpen ? (
    <Button
      type="button"
      variant="ghost"
      onClick={onOpen}
      aria-label={t("design.card.openDesign")}
      data-design-frame={frame.document_id}
      className="flex h-auto w-full flex-wrap justify-start gap-2 rounded-chip border border-border p-2 text-sm hover:bg-muted/70"
    >
      {content}
    </Button>
  ) : (
    <div className="flex flex-wrap items-center gap-2 text-sm" data-design-frame={frame.document_id}>
      {content}
    </div>
  );
}
