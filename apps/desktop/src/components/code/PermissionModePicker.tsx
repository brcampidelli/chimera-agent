import { useEffect, useRef, useState } from "react";
import { BellOff, ChevronDown, Hand, ListChecks, Pencil, ShieldCheck, ShieldOff, type LucideIcon } from "lucide-react";

import { focusRing } from "@/components/ui/focus";
import { useT, type TFunc } from "@/lib/i18n";
import { PERMISSION_MODES, type PermissionMode } from "@/lib/permission-modes";
import { cn } from "@/lib/utils";

/** The icon of each mode. Written out per mode, not looked up from a generated name. */
const ICONS: Record<PermissionMode, LucideIcon> = {
  manual: Hand,
  accept_edits: Pencil,
  plan: ListChecks,
  auto: ShieldCheck,
  dont_ask: BellOff,
  bypass: ShieldOff,
};

/** Written out, not built as `composer.permission.${mode}`: the dead-key gate reads literal keys only. */
function modeLabel(t: TFunc, mode: PermissionMode): string {
  switch (mode) {
    case "manual":
      return t("composer.permission.manual");
    case "accept_edits":
      return t("composer.permission.acceptEdits");
    case "plan":
      return t("composer.permission.plan");
    case "auto":
      return t("composer.permission.auto");
    case "dont_ask":
      return t("composer.permission.dontAsk");
    case "bypass":
      return t("composer.permission.bypass");
  }
}

function modeHint(t: TFunc, mode: PermissionMode): string {
  switch (mode) {
    case "manual":
      return t("composer.permission.manual.hint");
    case "accept_edits":
      return t("composer.permission.acceptEdits.hint");
    case "plan":
      return t("composer.permission.plan.hint");
    case "auto":
      return t("composer.permission.auto.hint");
    case "dont_ask":
      return t("composer.permission.dontAsk.hint");
    case "bypass":
      return t("composer.permission.bypass.hint");
  }
}

/**
 * The one selector for how far the agent may act without asking, under the prompt.
 *
 * It answers for the conversation: the choice is held by the Code screen, reset when the
 * conversation changes, and read from Settings when nothing was chosen. `Ignore permissions` is listed
 * only when the owner has released it, here or for the project, because it is the owner's switch and
 * not a mode a person picks in passing. While the project holds it, every other mode is locked: the
 * switch is the project's, and this selector cannot turn a project's switch off.
 */
export function PermissionModePicker({
  value,
  onChange,
  bypassListed,
  locked = false,
  disabled = false,
  openRequest = false,
  onOpenRequestHandled,
}: {
  value: PermissionMode;
  onChange: (mode: PermissionMode) => void;
  /** Whether "Ignore permissions" is offered: the owner released it for this project or conversation. */
  bypassListed: boolean;
  /** True when the project holds the switch on: only "Ignore permissions" can be chosen here. */
  locked?: boolean;
  disabled?: boolean;
  /** True while the ⌘⇧M shortcut asks for the list to open. */
  openRequest?: boolean;
  onOpenRequestHandled?: () => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const Icon = ICONS[value];
  const current = modeLabel(t, value);
  const shown = PERMISSION_MODES.filter((mode) => mode !== "bypass" || bypassListed);

  useEffect(() => {
    if (!openRequest) return;
    setOpen(true);
    onOpenRequestHandled?.();
  }, [openRequest, onOpenRequestHandled]);

  // Opens on the mode in force, or the first one that can be chosen: a locked list's first row is disabled.
  useEffect(() => {
    if (!open) return;
    const pick =
      list.current?.querySelector<HTMLButtonElement>('button[role="option"][aria-selected="true"]:not(:disabled)') ??
      list.current?.querySelector<HTMLButtonElement>('button[role="option"]:not(:disabled)');
    pick?.focus();
  }, [open]);

  function close(restoreFocus: boolean) {
    setOpen(false);
    if (restoreFocus) trigger.current?.focus();
  }

  return (
    <div className="relative inline-flex flex-col items-start gap-1" data-testid="permission-mode-picker">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`${t("composer.permission.label")}: ${current}`}
        title={modeHint(t, value)}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && open) close(true);
        }}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-chip border border-accent/60 px-2.5 py-1 text-xs text-accent-ink",
          "transition-colors duration-1 ease-out hover:bg-surface-hover disabled:opacity-50",
          focusRing,
        )}
      >
        <Icon aria-hidden className="h-3.5 w-3.5" />
        <span>{current}</span>
        <ChevronDown aria-hidden className="h-3 w-3" />
      </button>
      {open ? (
        <div
          ref={list}
          role="listbox"
          aria-label={t("composer.permission.label")}
          onKeyDown={(e) => {
            if (e.key === "Escape") return close(true);
            if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
            // Arrows move between the choices that can be taken; a locked row is skipped.
            const options = Array.from(list.current?.querySelectorAll<HTMLButtonElement>('button[role="option"]:not(:disabled)') ?? []);
            if (options.length === 0) return;
            e.preventDefault();
            const at = options.indexOf(document.activeElement as HTMLButtonElement);
            const down = e.key === "ArrowDown";
            const next = at < 0 ? (down ? 0 : options.length - 1) : (down ? at + 1 : at - 1 + options.length) % options.length;
            options[next]?.focus();
          }}
          className="z-10 flex w-72 max-w-full flex-col gap-0.5 rounded-card border border-hairline bg-surface p-1 shadow-md"
        >
          {shown.map((mode) => {
            const ModeIcon = ICONS[mode];
            const selected = mode === value;
            const blocked = disabled || (locked && mode !== "bypass");
            return (
              <button
                key={mode}
                type="button"
                role="option"
                aria-selected={selected}
                disabled={blocked}
                onClick={() => {
                  close(true);
                  if (!selected) onChange(mode);
                }}
                className={cn(
                  "flex items-start gap-2 rounded-chip px-2 py-1.5 text-left transition-colors duration-1 ease-out",
                  "hover:bg-surface-hover disabled:opacity-50",
                  selected ? "bg-accent/15" : "",
                  focusRing,
                )}
              >
                <ModeIcon aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <span className="flex min-w-0 flex-col">
                  <span className="text-xs text-foreground">{modeLabel(t, mode)}</span>
                  <span className="text-xs text-muted-foreground">{modeHint(t, mode)}</span>
                </span>
              </button>
            );
          })}
          {locked ? (
            <p className="px-2 pb-1 pt-1 text-xs text-muted-foreground">{t("composer.permission.locked")}</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
