import { useId, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Switch } from "@/components/ui/switch";
import { getShellPrefs, patchShellPrefs } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { ShellPrefs, ShellPrefsChange } from "@/lib/types";

/** How often the card re-reads the shell's file while it is on screen, and while a sign-in request
 *  waits for the shell. Each read is one small local file through the backend. */
const POLL_MS = 5000;
const PENDING_POLL_MS = 2000;

/** The chord as a person reads it: `CommandOrControl` is Cmd on a Mac and Ctrl everywhere else —
 *  the same reading the tray menu gives it (`chord_label` in main.rs). */
export function chordLabel(chord: string): string {
  const mac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform ?? "");
  return chord
    .split("+")
    .map((part) =>
      /^(commandorcontrol|commandorctrl|cmdorctrl|cmdorcontrol)$/i.test(part.trim())
        ? mac
          ? "Cmd"
          : "Ctrl"
        : part.trim(),
    )
    .join("+");
}

/**
 * Settings › General › Window and tray: the desktop app's own four switches, which until now only
 * its tray menu could change.
 *
 * They act on the native process — whether closing the window ends the app, whether the taskbar
 * flashes, which global chord it holds, whether it starts at sign-in — so the shell keeps them in a
 * file of its own. The window has no IPC to the shell, deliberately, so the backend writes that file
 * for this card (`chimera/api/shell_prefs.py`) and the shell takes the change in on its next look,
 * a few seconds later. The tray's check items follow.
 *
 * Start-at-sign-in shows what the operating system answered, as the shell reported it, and never the
 * click: a refused sign-in entry has to read as off. Between the click and the report the row says
 * it is waiting, and the card asks again until the report arrives.
 */
export function ShellCard() {
  const t = useT();
  const headingId = useId();
  const qc = useQueryClient();
  const prefs = useQuery({
    queryKey: ["shell-prefs"],
    // Wrapped, like the keep-awake card: a screen older tests mount without this mock must fail the
    // query, not the render.
    queryFn: () => getShellPrefs(),
    retry: false,
    // Asked again while the card is on screen: the tray changes the same file, and a card that only
    // read it on mount would go on showing a switch the tray has since flipped. Faster while a
    // sign-in request is out, until the shell has carried it out and reported the OS's answer.
    refetchInterval: (query) =>
      query.state.data?.sign_in_requested != null ? PENDING_POLL_MS : POLL_MS,
  });
  const mutation = useMutation({
    mutationFn: (change: ShellPrefsChange) => patchShellPrefs(change),
    onSuccess: (next: ShellPrefs) => qc.setQueryData(["shell-prefs"], next),
  });
  const p = prefs.data;
  // A server older than the route answers 404; that is "not available here", not an error to show.
  if (!p) return null;

  const save = (change: ShellPrefsChange) => mutation.mutate(change);
  const locked = !p.available || p.unreadable || mutation.isPending;
  // The OS's answer as the shell reported it, never the request: until the shell has asked the
  // system, the switch stays where the system is, and the note below says a request is out.
  const signInShown = p.start_at_sign_in ?? false;
  const chord = chordLabel(p.quick_entry_chord || "CommandOrControl+Shift+Space");

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.shell")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">
          {p.available ? t("settings.shell.intro") : t("settings.shell.unavailable")}
        </p>
        {p.available && p.unreadable && (
          <p role="status" className="px-4 py-2.5 text-xs text-warn-foreground">
            {t("settings.shell.unreadable")}
          </p>
        )}
        <ShellRow label={t("settings.shell.keepInTray")} hint={t("settings.shell.keepInTrayHint")}>
          <Switch
            checked={p.keep_in_tray ?? false}
            disabled={locked}
            label={t("settings.shell.keepInTray")}
            onChange={(next) => save({ keep_in_tray: next })}
          />
        </ShellRow>
        <ShellRow label={t("settings.shell.attention")} hint={t("settings.shell.attentionHint")}>
          <Switch
            checked={p.call_attention ?? true}
            disabled={locked}
            label={t("settings.shell.attention")}
            onChange={(next) => save({ call_attention: next })}
          />
        </ShellRow>
        <ShellRow
          label={t("settings.shell.quickEntry")}
          hint={t("settings.shell.quickEntryHint", { chord })}
        >
          <Switch
            checked={p.quick_entry ?? false}
            disabled={locked}
            label={t("settings.shell.quickEntry")}
            onChange={(next) => save({ quick_entry: next })}
          />
        </ShellRow>
        <ShellRow
          label={t("settings.shell.signIn")}
          hint={t("settings.shell.signInHint")}
          note={
            p.sign_in_requested != null && p.sign_in_requested !== p.start_at_sign_in
              ? t("settings.shell.signInPending")
              : p.available && p.start_at_sign_in == null
                ? t("settings.shell.signInUnknown")
                : ""
          }
        >
          <Switch
            checked={signInShown}
            disabled={locked}
            label={t("settings.shell.signIn")}
            onChange={(next) => save({ start_at_sign_in: next })}
          />
        </ShellRow>
        {p.available && p.problem ? (
          <p role="status" className="px-4 py-2.5 text-xs text-warn-foreground">
            {t("settings.shell.problem", { problem: p.problem })}
          </p>
        ) : null}
        {mutation.isError && (
          <p role="alert" className="px-4 py-2.5 text-xs text-bad-foreground">
            {mutation.error instanceof Error ? mutation.error.message : String(mutation.error)}
          </p>
        )}
      </div>
    </section>
  );
}

function ShellRow({
  label,
  hint,
  note = "",
  children,
}: {
  label: string;
  hint: string;
  /** A second line about the row's state right now, in the warning colour. */
  note?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        <div className="text-xs text-muted-foreground">{hint}</div>
        {note ? <div className="text-xs text-warn-foreground">{note}</div> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">{children}</div>
    </div>
  );
}
