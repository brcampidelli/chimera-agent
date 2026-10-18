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

interface SearchEntry {
  tab: SearchableTab;
  label: string;
  hint?: string;
}

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
  { tab: "general", label: "settings.card.autonomy" },
  { tab: "general", label: "settings.card.bridge" },
  { tab: "general", label: "settings.card.sharing" },
  { tab: "general", label: "settings.card.server" },
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
  })).filter(({ label, hint }) => `${label} ${hint}`.toLocaleLowerCase().includes(needle));
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
  onOpen: (tab: SearchableTab) => void;
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
                onClick={() => onOpen(entry.tab)}
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
