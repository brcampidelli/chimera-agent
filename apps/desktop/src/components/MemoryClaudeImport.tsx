import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileInput } from "lucide-react";
import { applyClaudeImport, previewClaudeImport } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { ScopeBadge, invalidateMemory } from "@/components/MemoryScope";
import { useT } from "@/lib/i18n";
import type { ClaudeImportPreview } from "@/lib/types";

type Candidate = ClaudeImportPreview["candidates"][number];

/** A note about one Claude project that no registered folder matches: it would apply everywhere. */
const stray = (c: Candidate) => !c.project && !!c.claude_project;

/** Import from Claude: preview (writes nothing) → tick → import only what was ticked.
 *
 * Nothing is ticked by default. The review IS the selection, so a single click cannot import a
 * folder of notes the person has not read; "select all new" is there for when they have — and it
 * leaves out the stray project notes, which are the ones a reader is least likely to want
 * everywhere.
 *
 * The import writes from the folder that was PREVIEWED, and editing the folder field drops the
 * preview: with the field live under an open preview, changing it and pressing Import re-scanned
 * the new folder and wrote whatever ticked strings also appeared there — facts that did not come
 * from the folder that was reviewed, under a file name that was wrong for them. */
export function ClaudeImportSection({ sectionTitle }: { sectionTitle: string }) {
  const t = useT();
  const qc = useQueryClient();
  const [path, setPath] = useState("");
  const [preview, setPreview] = useState<ClaudeImportPreview | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [done, setDone] = useState<number | null>(null);
  const look = useMutation({
    mutationFn: () => previewClaudeImport(path),
    onSuccess: (p) => {
      setPreview(p);
      setChosen(new Set());
      setDone(null);
    },
  });
  const write = useMutation({
    mutationFn: (reviewed: ClaudeImportPreview) => applyClaudeImport(reviewed.path, [...chosen]),
    onSuccess: (r) => {
      setDone(r.counts.ADD ?? r.written);
      setPreview(null);
      setChosen(new Set());
      invalidateMemory(qc);
    },
  });
  const toggle = (content: string) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(content)) next.delete(content);
      else next.add(content);
      return next;
    });

  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      <div className={sectionTitle}>{t("memory.import.title")}</div>
      <p className="text-xs text-muted-foreground">{t("memory.import.hint")}</p>
      <p className="text-xs text-muted-foreground">{t("memory.import.scopeHint")}</p>
      <form
        className="flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          look.mutate();
        }}
      >
        <input
          className="field h-8 w-full px-2 text-sm"
          aria-label={t("memory.import.title")}
          placeholder="~/.claude"
          value={path}
          onChange={(e) => {
            setPath(e.target.value);
            setPreview(null);
            setChosen(new Set());
          }}
        />
        <Button size="sm" variant="outline" type="submit" disabled={look.isPending}>
          <FileInput className="h-4 w-4" /> {t("memory.preview")}
        </Button>
      </form>
      {look.isError && <ErrorState error={look.error} onRetry={() => look.mutate()} />}
      {done !== null && (
        <p className="text-xs text-ok-foreground" role="status">
          {t("memory.import.done", { n: done })}
        </p>
      )}
      {preview && preview.candidates.length === 0 && (
        <p className="text-xs text-muted-foreground">{t("memory.import.none")}</p>
      )}
      {preview && preview.candidates.length > 0 && (
        <>
          <div className="max-h-72 overflow-y-auto rounded-chip border border-hairline">
            {preview.candidates.map((c) => (
              <label key={c.content} className="flex items-start gap-2 px-3 py-1.5 text-sm hover:bg-surface-hover">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={chosen.has(c.content)}
                  disabled={c.known}
                  onChange={() => toggle(c.content)}
                />
                <span className="min-w-0 flex-1">
                  {c.content}
                  <span className="ml-2 font-mono text-xs text-muted-foreground">{c.file}</span>
                  {stray(c) && (
                    <span className="block text-xs text-warn-foreground">
                      {t("memory.import.strayHint", { slug: c.claude_project ?? "" })}
                    </span>
                  )}
                </span>
                <ScopeBadge project={c.project} fromProject={c.claude_project ?? ""} />
                {c.known && <Badge tone="muted">{t("memory.import.known")}</Badge>}
              </label>
            ))}
          </div>
          {preview.notes.map((n) => (
            <p key={n} className="text-xs text-muted-foreground">
              {n}
            </p>
          ))}
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                setChosen(new Set(preview.candidates.filter((c) => !c.known && !stray(c)).map((c) => c.content)))
              }
            >
              {t("memory.import.selectNew")}
            </Button>
            <Button size="sm" disabled={chosen.size === 0 || write.isPending} onClick={() => write.mutate(preview)}>
              {t("memory.import.apply", { n: chosen.size })}
            </Button>
          </div>
          {write.isError && <ErrorState error={write.error} onRetry={() => write.mutate(preview)} />}
        </>
      )}
    </div>
  );
}
