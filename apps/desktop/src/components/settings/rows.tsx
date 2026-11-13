import { createContext, useContext, useId, type ReactNode } from "react";

import { Switch } from "@/components/ui/switch";
import { useT } from "@/lib/i18n";

/**
 * The card, row and toggle every settings screen is built from.
 *
 * They lived inside Settings.tsx, so a screen that wanted to look like Settings — a card of labelled
 * rows, each saying when a change applies and whether the server's environment pins it — copied them
 * or did without. Moved here unchanged, so there is one of each.
 */

export function Card({
  id,
  title,
  children,
}: {
  /** Where a search result or a link lands; see `SearchTarget` in SettingsSearch.tsx. */
  id?: string;
  title: string;
  children: ReactNode;
}) {
  // Named region rather than a bare section: this screen stacks eleven of them, and unnamed they
  // announce as eleven identical "section" landmarks with no way to tell which one you are in.
  const headingId = useId();
  return (
    <section id={id} className="surface overflow-hidden" aria-labelledby={headingId}>
      {/* Focusable by script only (no tab stop): a search result or a link lands the focus here when
          the card has no control to land on. */}
      <h2
        id={headingId}
        tabIndex={-1}
        className="border-b border-hairline px-4 py-2.5 text-sm font-semibold"
      >
        {title}
      </h2>
      <div className="divide-y divide-hairline">{children}</div>
    </section>
  );
}

/** The label of the enclosing Row, so a control inside it can name itself without repeating it. */
export const RowLabelContext = createContext("");

/** When a saved change starts applying, for the settings where the answer is not "the next call".
 *
 * The value comes from the server (`config.applies`), never from a list kept here: the answer is a
 * property of where the setting is READ, so a copy on this side would go stale the first time a read
 * moves — and it would go stale silently, which is the whole failure this label exists to stop. A
 * control that confirms and does nothing spends the user's trust in every other control on screen.
 */
export function AppliesNote({ when }: { when?: string }) {
  const t = useT();
  // `commands_now`: two moments, not one. What builds its sandbox per use (a `!` command, a
  // workflow, the verifier) takes the save at once; an open chat keeps the tools it was built with.
  // `now` is the plain case, stated since an absent entry stopped meaning it. `unverified` (and
  // anything the server adds that this build does not know) says nothing, on purpose: a sentence
  // about a setting nobody traced is the confident wrong answer this label exists to stop.
  const key =
    when === "next_launch"
      ? "settings.applies.nextLaunch"
      : when === "next_conversation"
        ? "settings.applies.nextConversation"
        : when === "commands_now"
          ? "settings.applies.commandsNow"
          : when === "now"
            ? "settings.applies.now"
            : null;
  if (!key) return null;
  // The two later moments are a caveat and read as one; "now" is good news and reads quieter.
  return <div className={when === "now" ? "text-xs text-muted-foreground" : "text-xs text-warn-foreground"}>{t(key)}</div>;
}

/**
 * What the server says about when each saved setting applies (`config.applies`), for the Rows that
 * name their variable. A Row used to be handed it one by one and most were not, so most rows said
 * nothing; read here by `env`, every row that names a variable can say it, and an explicit
 * `applies` prop still wins.
 */
export const AppliesContext = createContext<Readonly<Record<string, string>>>({});

/** The env vars this server inherited from its own environment, so a Row can say it is one of them. */
export const PinnedContext = createContext<ReadonlySet<string>>(new Set());

/**
 * When the server's environment outranks the save this screen is about to make.
 *
 * A real environment variable beats `.env`, and `PATCH /api/config` writes `.env`. So for a key the
 * server was STARTED with, Save succeeds, the value holds for the whole session (the patch writes
 * `os.environ` too) and then reverts at the next launch. The delay is what makes this worse than a
 * refusal: by the time the old value is back, nobody connects it to the save, and the screen has
 * spent its credibility on a control that reported a change it could not keep.
 *
 * The list comes from the server (`config.pinned`), like `applies` and for the same reason: only the
 * process that was started knows what it was started with, and a copy kept here would be a guess
 * about someone else's deployment. It is normally empty — this is a container / systemd / remote
 * concern, reachable at all because the app can be pointed at a server somebody else runs.
 */
export function PinnedNote({ env }: { env?: string }) {
  const t = useT();
  const pinned = useContext(PinnedContext);
  if (!env || !pinned.has(env)) return null;
  return <div className="text-xs text-warn-foreground">{t("settings.pinned")}</div>;
}

export function Row({
  label,
  hint,
  applies,
  env,
  warn = false,
  note,
  children,
}: {
  label: string;
  hint?: string;
  applies?: string;
  /** The single env var this row writes, so the row can say when the server's environment pins it. */
  env?: string;
  /** The hint is a warning about what switching this on allows, not a description of it. */
  warn?: boolean;
  /** A live line under the hint — a figure the server measured, not a sentence written in advance. */
  note?: ReactNode;
  children: ReactNode;
}) {
  const appliesByEnv = useContext(AppliesContext);
  const moment = applies ?? (env ? appliesByEnv[env] : undefined);
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <div className="min-w-0">
        <div className="text-sm font-medium">{label}</div>
        {hint && (
          <div className={warn ? "text-xs text-warn-foreground" : "text-xs text-muted-foreground"}>
            {hint}
          </div>
        )}
        {note}
        <AppliesNote when={moment} />
        <PinnedNote env={env} />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <RowLabelContext.Provider value={label}>
          {children}
        </RowLabelContext.Provider>
      </div>
    </div>
  );
}

export function Toggle({
  on,
  onChange,
  id,
}: {
  on: boolean;
  onChange: (v: boolean) => void;
  id?: string;
}) {
  // Was a near-copy of the one in Cron.tsx. Now the shared primitive, named from its Row — before
  // this, all six of these announced as an unlabelled "switch".
  return (
    <Switch
      id={id}
      checked={on}
      onChange={onChange}
      label={useContext(RowLabelContext)}
    />
  );
}
