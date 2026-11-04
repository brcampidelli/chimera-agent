import { Fragment } from "react";

import { useT, type TFunc } from "@/lib/i18n";

/**
 * Search across Settings by what the person reads: the translated label and description of each
 * option, never a list of English keywords kept beside them. An option found here opens the tab that
 * holds it.
 *
 * The index names i18n keys, not strings, so a result is in the interface language and a search for
 * "idioma" finds "Idioma" without a second vocabulary to maintain.
 */
export type SearchableTab = "general" | "appearance" | "security";

/**
 * Where a result lands. `anchor` is the id of the element to reveal: a switch, or a whole card.
 * `advanced` says it sits inside the Advanced block, which is closed until opened, so the screen has
 * to open it first. Before this existed, a result switched tabs and stopped there, and the card it
 * named was out of sight.
 */
export interface SearchTarget {
  tab: SearchableTab;
  anchor?: string;
  advanced?: boolean;
}

interface SearchEntry extends SearchTarget {
  label: string;
  hint?: string;
  /** Words to search by beyond the label and hint: an i18n key whose value is a comma-separated list. */
  terms?: string;
}

const inAdvanced = (anchor: string): SearchTarget => ({ tab: "general", anchor, advanced: true });

const ENTRIES: readonly SearchEntry[] = [
  { tab: "appearance", label: "settings.row.language", hint: "settings.hint.language" },
  { tab: "appearance", label: "settings.row.theme", hint: "settings.hint.theme" },
  { tab: "appearance", label: "settings.row.accent" },
  { tab: "appearance", label: "settings.row.motion" },
  { tab: "appearance", label: "settings.row.textSize" },
  { tab: "appearance", label: "settings.row.transcriptWidth" },
  { tab: "appearance", label: "settings.row.transcriptView" },
  { tab: "general", label: "code.sessions.defaultProject", hint: "code.workspacePlaceholder" },
  { tab: "general", label: "settings.row.defaultModel" },
  { tab: "general", label: "code.style.label", hint: "profile.style.contextual" },
  { tab: "general", label: "settings.row.agentName", hint: "settings.hint.agentName" },
  { tab: "general", label: "settings.row.agentLanguage" },
  { tab: "general", label: "settings.row.voiceModel", hint: "settings.hint.voiceModel" },
  { tab: "general", label: "settings.row.voiceWorkModel", hint: "settings.hint.voiceWorkModel" },
  { tab: "general", label: "notify.row.finish" },
  { tab: "general", label: "notify.row.minSeconds", hint: "notify.hint.minSeconds" },
  { tab: "general", label: "notify.row.approvals", hint: "notify.hint.approvals" },
  { tab: "general", label: "settings.row.costMode", hint: "settings.hint.costMode" },
  {
    tab: "general",
    label: "settings.card.autonomy",
    anchor: "settings-card-autonomy",
    terms: "settings.search.terms.autonomy",
  },
  // The two agent-authority switches sit on the autonomy card, on General, not in the Advanced block.
  {
    tab: "general",
    label: "settings.row.pullRequests",
    hint: "settings.hint.pullRequests",
    anchor: "settings-pull-requests",
    terms: "settings.search.terms.pullRequests",
  },
  {
    tab: "general",
    label: "settings.row.closeLoop",
    hint: "settings.hint.closeLoop",
    anchor: "settings-close-loop",
    terms: "settings.search.terms.closeLoop",
  },
  // The Security screen's Access card: the browser's way to the owner's screen (a code, then a session).
  {
    tab: "security",
    label: "browserPairing.title",
    anchor: "settings-browser-pairing",
    terms: "settings.search.terms.browserPairing",
  },
  // The Advanced block: every card in it is indexed, because a closed block hides what the search
  // leaves out. The two bridge switches are indexed by row as well, so "ponte" lands on the switch.
  { ...inAdvanced("settings-card-pools"), label: "settings.card.pools" },
  { ...inAdvanced("settings-card-memory"), label: "settings.card.memory" },
  { ...inAdvanced("settings-card-cache-sandbox"), label: "settings.card.cacheSandbox" },
  { ...inAdvanced("settings-card-mcp"), label: "settings.card.mcp" },
  { ...inAdvanced("settings-card-automation"), label: "settings.card.automation" },
  { ...inAdvanced("settings-card-keep-awake"), label: "settings.card.keepAwake" },
  { ...inAdvanced("settings-card-experimental"), label: "settings.card.experimental" },
  { ...inAdvanced("settings-card-system-one"), label: "settings.card.systemOne" },
  { ...inAdvanced("settings-card-storage"), label: "settings.card.storage" },
  { ...inAdvanced("settings-card-diagnostics"), label: "settings.card.diagnostics" },
  { ...inAdvanced("settings-card-server"), label: "settings.card.server" },
  { ...inAdvanced("settings-card-sharing"), label: "settings.card.sharing" },
  { ...inAdvanced("settings-card-bridge"), label: "settings.card.bridge", terms: "settings.search.terms.bridge" },
  {
    ...inAdvanced("settings-desktop-bridge"),
    label: "settings.row.desktopBridge",
    hint: "settings.hint.desktopBridge",
    terms: "settings.search.terms.bridge",
  },
  {
    ...inAdvanced("settings-desktop-bridge-full"),
    label: "settings.row.desktopBridgeFull",
    hint: "settings.hint.desktopBridgeFull",
    terms: "settings.search.terms.bridge",
  },
];

interface Match {
  entry: SearchEntry;
  label: string;
  hint: string;
}

function search(query: string, t: TFunc): Match[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [];
  return ENTRIES.map((entry) => ({
    entry,
    label: t(entry.label),
    hint: entry.hint ? t(entry.hint) : "",
    terms: entry.terms ? t(entry.terms) : "",
  })).filter(({ label, hint, terms }) => `${label} ${hint} ${terms}`.toLocaleLowerCase().includes(needle));
}

/** The text with every occurrence of the query wrapped in <mark>. */
function Highlight({ text, query }: { text: string; query: string }) {
  const needle = query.trim().toLocaleLowerCase();
  const parts: { text: string; hit: boolean }[] = [];
  const lower = text.toLocaleLowerCase();
  let at = 0;
  for (let i = lower.indexOf(needle); i !== -1 && needle; i = lower.indexOf(needle, at)) {
    if (i > at) parts.push({ text: text.slice(at, i), hit: false });
    parts.push({ text: text.slice(i, i + needle.length), hit: true });
    at = i + needle.length;
  }
  parts.push({ text: text.slice(at), hit: false });
  return (
    <>
      {parts.map((part, index) => (
        <Fragment key={index}>
          {part.hit ? <mark className="rounded-sm bg-accent/20 text-foreground">{part.text}</mark> : part.text}
        </Fragment>
      ))}
    </>
  );
}

export function SettingsSearchResults({
  query,
  tabLabel,
  onOpen,
}: {
  query: string;
  tabLabel: (tab: SearchableTab) => string;
  onOpen: (target: SearchTarget) => void;
}) {
  const t = useT();
  const matches = search(query, t);
  return (
    <div className="max-w-5xl px-6 py-6" role="region" aria-label={t("settings.search.results")}>
      {matches.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("settings.search.none", { query: query.trim() })}</p>
      ) : (
        <ul className="divide-y divide-hairline overflow-hidden rounded-card border border-border">
          {matches.map(({ entry, label, hint }) => (
            <li key={entry.label}>
              <button
                type="button"
                className="w-full px-4 py-3 text-left hover:bg-surface-hover"
                onClick={() => onOpen({ tab: entry.tab, anchor: entry.anchor, advanced: entry.advanced })}
              >
                <span className="text-sm font-medium">
                  <Highlight text={label} query={query} />
                </span>
                <span className="ml-2 text-xs text-muted-foreground">{tabLabel(entry.tab)}</span>
                {hint && (
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    <Highlight text={hint} query={query} />
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
