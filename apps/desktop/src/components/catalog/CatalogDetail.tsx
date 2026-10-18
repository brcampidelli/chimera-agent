import * as RadixDialog from "@radix-ui/react-dialog";
import { Check, X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/**
 * The detail of one catalog item, as a panel on the right (study 04, §2.7).
 *
 * Radix Dialog for the same reason `ui/dialog.tsx` uses it: the focus trap, Escape, the inert rest
 * of the page and the scroll lock are the parts everyone's first version gets wrong. The caller
 * drives `open` from the URL (`item=`), so a pasted link opens it and the browser's Back closes it;
 * this component only says when the person asked to close.
 *
 * Focus goes back to whatever opened it — the card's title — rather than to `<body>`. Radix only
 * restores to its own Trigger, and a card is not one, so the element is remembered here, exactly
 * as the modal dialog does.
 */
export function CatalogDetail({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: ReactNode;
  /** The step-by-step of installing, reviewing and switching on — fixed at the bottom. */
  footer?: ReactNode;
}) {
  const t = useT();
  const restoreTo = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (open) restoreTo.current = document.activeElement as HTMLElement | null;
  }, [open]);

  return (
    <RadixDialog.Root open={open} onOpenChange={(next) => (next ? undefined : onClose())}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="backdrop fixed inset-0 z-50 bg-scrim" />
        <RadixDialog.Content
          {...(subtitle ? {} : { "aria-describedby": undefined })}
          onCloseAutoFocus={(e) => {
            const target = restoreTo.current;
            if (!target || !target.isConnected) return;
            e.preventDefault();
            target.focus();
          }}
          className="overlay floating fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col rounded-r-none"
        >
          <div className="flex items-start justify-between gap-4 border-b border-hairline p-5">
            <div className="min-w-0">
              <RadixDialog.Title className="text-lg font-semibold">{title}</RadixDialog.Title>
              {subtitle ? (
                <RadixDialog.Description className="mt-1 text-xs text-muted-foreground">
                  {subtitle}
                </RadixDialog.Description>
              ) : null}
            </div>
            <RadixDialog.Close
              className={cn(
                "-mr-1 -mt-1 rounded-lg p-1.5 text-muted-foreground transition-colors duration-1 ease-out hover:text-foreground",
                focusRing,
              )}
            >
              <X aria-hidden className="h-4 w-4" />
              <span className="sr-only">{t("common.close")}</span>
            </RadixDialog.Close>
          </div>
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">{children}</div>
          {footer ? <div className="border-t border-hairline p-5">{footer}</div> : null}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

export type StepState = "done" | "current" | "todo";

export interface DetailStep {
  id: string;
  label: string;
  state: StepState;
  /** The control that takes this step. Drawn on the current step, and on any step whose control
   *  stays useful after it is done (a switch can be turned off again). */
  action?: ReactNode;
  /** A line under the label: what was done, or why the step cannot be taken. */
  note?: ReactNode;
}

/**
 * Install → Review → Switch on, as separate steps because they are separate decisions. Done steps
 * keep a check and a word; only the next one carries the primary control. Nothing here decides who
 * may take a step: switching on stays an owner-only route the bridge cannot reach, whatever this
 * draws.
 */
export function DetailSteps({ steps }: { steps: readonly DetailStep[] }) {
  const t = useT();
  return (
    <ol aria-label={t("catalogUi.steps")} className="space-y-3">
      {steps.map((step, i) => (
        <li key={step.id} data-step={step.id} data-state={step.state} className="flex items-start gap-3">
          <span
            aria-hidden
            className={cn(
              "flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ring-1",
              step.state === "done" && "bg-ok/15 text-ok-foreground ring-ok/25",
              step.state === "current" && "bg-accent/15 text-accent-ink ring-accent/25",
              step.state === "todo" && "bg-surface-2 text-muted-foreground ring-hairline",
            )}
          >
            {step.state === "done" ? <Check className="h-3.5 w-3.5" /> : i + 1}
          </span>
          <div className="min-w-0 flex-1 space-y-1">
            <p className={cn("text-sm", step.state === "todo" ? "text-muted-foreground" : "font-semibold")}>
              {step.label}
              {step.state === "done" ? <span className="sr-only">{` — ${t("catalogUi.stepDone")}`}</span> : null}
            </p>
            {step.note ? <div className="text-xs text-muted-foreground">{step.note}</div> : null}
          </div>
          {step.action ? <div className="flex shrink-0 items-center gap-2">{step.action}</div> : null}
        </li>
      ))}
    </ol>
  );
}
