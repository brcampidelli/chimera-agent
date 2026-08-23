import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CaseSensitive, Regex } from "lucide-react";

import { searchFiles } from "@/lib/api";
import type { SearchHit } from "@/lib/types";
import { cn } from "@/lib/utils";
import { focusRing } from "@/components/ui/focus";
import { Spinner } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";

/**
 * Find a string across the workspace, and open where it is.
 *
 * The agent has had `grep_files` since the beginning; a person looking at the editor has had
 * nothing. Those are different needs — a tool call returns a blob for a model to read, and this
 * returns places to click.
 *
 * Results are grouped by file because that is how the answer is actually used: "which files is this
 * in" first, "which lines" second. A flat list of two hundred hits answers the second question at
 * the cost of the first.
 */

function Hits({
  hits,
  activePath,
  onOpen,
}: {
  hits: SearchHit[];
  activePath: string | null;
  onOpen: (path: string, line: number) => void;
}) {
  const byFile = new Map<string, SearchHit[]>();
  for (const hit of hits) {
    const existing = byFile.get(hit.path);
    if (existing) existing.push(hit);
    else byFile.set(hit.path, [hit]);
  }

  return (
    <div className="flex flex-col">
      {[...byFile.entries()].map(([path, fileHits]) => (
        <div key={path}>
          <p
            className={cn(
              "sticky top-0 truncate bg-card/80 px-2 py-1 text-xs backdrop-blur",
              path === activePath ? "text-accent" : "text-muted-foreground",
            )}
            title={path}
          >
            {path} <span className="opacity-60">({fileHits.length})</span>
          </p>
          {fileHits.map((hit) => (
            <button
              key={`${hit.line}:${hit.start}`}
              type="button"
              onClick={() => onOpen(hit.path, hit.line)}
              className={cn(
                "flex w-full items-baseline gap-2 px-2 py-0.5 text-left font-mono text-xs",
                "transition-colors duration-1 ease-out hover:bg-surface-hover",
                focusRing,
              )}
            >
              <span className="w-8 shrink-0 text-right text-muted-foreground">{hit.line}</span>
              {/* Highlighted from the offsets the server measured, never by re-searching the line
                  here — a case-insensitive or regex query re-searched in the browser highlights the
                  wrong span, and looks like a rendering bug rather than a wrong answer. */}
              <span className="truncate text-muted-foreground">
                {hit.text.slice(0, hit.start)}
                <mark className="bg-accent/25 text-foreground">
                  {hit.text.slice(hit.start, hit.end)}
                </mark>
                {hit.text.slice(hit.end)}
              </span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

export function SearchPanel({
  workspace,
  activePath,
  onOpen,
}: {
  workspace: string | null;
  activePath: string | null;
  onOpen: (path: string, line: number) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState("");
  // Submitted, not typed. A search per keystroke is a subprocess per keystroke on a repository that
  // may take a second to walk — and the results flicker between prefixes of what you meant.
  const [query, setQuery] = useState("");
  const [regex, setRegex] = useState(false);
  const [caseSensitive, setCaseSensitive] = useState(false);

  const result = useQuery({
    queryKey: ["search", workspace, query, regex, caseSensitive],
    queryFn: () => searchFiles(workspace, query, { regex, caseSensitive }),
    enabled: query.length > 0,
  });

  return (
    <div className="flex h-full flex-col">
      <form
        className="flex shrink-0 items-center gap-1 border-b border-hairline p-2"
        onSubmit={(e) => {
          e.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("edit.search.placeholder")}
          aria-label={t("edit.search.placeholder")}
          className="field min-w-0 flex-1 px-2 text-xs"
        />
        <button
          type="button"
          onClick={() => setCaseSensitive((v) => !v)}
          aria-pressed={caseSensitive}
          title={t("edit.search.caseSensitive")}
          aria-label={t("edit.search.caseSensitive")}
          className={cn(
            "rounded p-1 transition-colors duration-1 ease-out",
            focusRing,
            caseSensitive ? "bg-accent/20 text-accent" : "text-muted-foreground",
          )}
        >
          <CaseSensitive className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => setRegex((v) => !v)}
          aria-pressed={regex}
          title={t("edit.search.regex")}
          aria-label={t("edit.search.regex")}
          className={cn(
            "rounded p-1 transition-colors duration-1 ease-out",
            focusRing,
            regex ? "bg-accent/20 text-accent" : "text-muted-foreground",
          )}
        >
          <Regex className="h-3.5 w-3.5" />
        </button>
      </form>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {result.isFetching ? (
          <div className="flex items-center justify-center py-4">
            <Spinner />
          </div>
        ) : result.data ? (
          <>
            {result.data.error ? (
              <p className="p-2 text-xs text-bad-foreground">{result.data.error}</p>
            ) : result.data.hits.length === 0 ? (
              <p className="p-2 text-xs text-muted-foreground">{t("edit.search.none")}</p>
            ) : (
              <Hits hits={result.data.hits} activePath={activePath} onOpen={onOpen} />
            )}
            {/* Both of these are the difference between "there are no more" and "we stopped
                looking", and only one of them means the symbol is unused. */}
            {result.data.capped ? (
              <p className="p-2 text-xs text-warn-foreground">{t("edit.search.capped")}</p>
            ) : null}
            {result.data.timed_out ? (
              <p className="p-2 text-xs text-warn-foreground">{t("edit.search.timedOut")}</p>
            ) : null}
            {/* The fallback names itself. A silent swap to a slower engine that ignores .gitignore
                would return different results for the same query on a different machine. */}
            {result.data.engine === "python" ? (
              <p className="p-2 text-xs text-muted-foreground">{t("edit.search.fallback")}</p>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
