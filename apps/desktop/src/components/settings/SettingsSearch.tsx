import { Fragment } from "react";

import { SEARCH_INDEX, type SearchEntry, type SearchableTab, type SearchTarget } from "@/components/settings/searchIndex";
import { useT, type TFunc } from "@/lib/i18n";

/**
 * Search across Settings by what the person reads: the translated label and description of each
 * option, never a list of English keywords kept beside them. An option found here opens the tab that
 * holds it. What can be found is in `searchIndex.ts`.
 */
export type { SearchableTab, SearchTarget };

interface Match {
  entry: SearchEntry;
  label: string;
  hint: string;
}

function search(query: string, t: TFunc): Match[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return [];
  return SEARCH_INDEX.map((entry) => ({
    entry,
    label: t(entry.label, entry.params),
    hint: entry.hint ? t(entry.hint, entry.params) : "",
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
            <li key={`${entry.label}:${JSON.stringify(entry.params ?? {})}`}>
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
