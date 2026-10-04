import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, Merge } from "lucide-react";
import { applyConsolidation, exportMemory, previewConsolidation } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Panel } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { ClaudeImportSection } from "@/components/MemoryClaudeImport";
import { ScopeBadge, invalidateMemory } from "@/components/MemoryScope";
import { useT } from "@/lib/i18n";
import type { ConsolidatePreview } from "@/lib/types";

const sectionTitle = "text-xs font-semibold uppercase tracking-wider text-muted-foreground";

/** Hand the export to the person as a file. Same approach as the conversation export: a Blob
 *  download, and — because whether an anchor click downloads inside the Tauri webview is the
 *  uncertain half — the clipboard as the fallback, said out loud. */
async function saveFile(name: string, type: string, text: string): Promise<boolean> {
  try {
    const url = URL.createObjectURL(new Blob([text], { type }));
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
    return true;
  } catch {
    await navigator.clipboard?.writeText(text).catch(() => undefined);
    return false;
  }
}

function ExportSection() {
  const t = useT();
  const [note, setNote] = useState("");
  const run = useMutation({
    // Wrapped, not passed: the mutation hands its function a second argument (its context), and
    // `exportMemory` would read it as a second parameter the day it grows one.
    mutationFn: (format: "json" | "markdown") => exportMemory(format),
    onSuccess: async (file) => {
      const saved = await saveFile(file.filename, file.media_type, file.content);
      setNote(saved ? "" : t("memory.export.toClipboard"));
    },
  });
  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={run.isPending} onClick={() => run.mutate("json")}>
          <Download className="h-4 w-4" /> {t("memory.export.json")}
        </Button>
        <Button size="sm" variant="outline" disabled={run.isPending} onClick={() => run.mutate("markdown")}>
          <Download className="h-4 w-4" /> {t("memory.export.markdown")}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{t("memory.export.hint")}</p>
      {note && <p className="text-xs text-muted-foreground" role="status">{note}</p>}
      {run.isError && <ErrorState error={run.error} onRetry={() => run.reset()} />}
    </div>
  );
}

/** Merge similar facts: a free preview of the clusters, then one model call per ticked group. */
function ConsolidateSection() {
  const t = useT();
  const qc = useQueryClient();
  const [preview, setPreview] = useState<ConsolidatePreview | null>(null);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [result, setResult] = useState<{ merged: number; stale: number; skipped: number } | null>(null);
  const look = useMutation({
    mutationFn: () => previewConsolidation(),
    onSuccess: (p) => {
      setPreview(p);
      setChosen(new Set());
      setResult(null);
    },
  });
  const merge = useMutation({
    mutationFn: (groups: string[][]) => applyConsolidation(groups),
    onSuccess: (r) => {
      setResult({ merged: r.merged, stale: r.stale, skipped: r.skipped });
      setPreview(null);
      setChosen(new Set());
      invalidateMemory(qc);
    },
  });
  const toggle = (i: number) =>
    setChosen((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      <div className={sectionTitle}>{t("memory.consolidate.title")}</div>
      <p className="text-xs text-muted-foreground">{t("memory.consolidate.hint")}</p>
      <div>
        <Button size="sm" variant="outline" disabled={look.isPending} onClick={() => look.mutate()}>
          <Merge className="h-4 w-4" /> {t("memory.preview")}
        </Button>
      </div>
      {look.isError && <ErrorState error={look.error} onRetry={() => look.mutate()} />}
      {result && (
        <p className="text-xs text-ok-foreground" role="status">
          {t("memory.consolidate.done", { merged: result.merged, stale: result.stale })}
        </p>
      )}
      {/* Said apart from "merged": a group the model answered blank is unchanged, yet was paid for. */}
      {result && result.skipped > 0 && (
        <p className="text-xs text-warn-foreground">
          {t("memory.consolidate.skipped", { skipped: result.skipped })}
        </p>
      )}
      {preview && preview.groups.length === 0 && (
        <p className="text-xs text-muted-foreground">{t("memory.consolidate.none")}</p>
      )}
      {preview && preview.groups.length > 0 && (
        <>
          {preview.groups.map((g, i) => (
            <label key={g.items.map((it) => it.id).join(",")} className="flex items-start gap-2 rounded-chip border border-hairline px-3 py-2">
              <input type="checkbox" className="mt-1" checked={chosen.has(i)} onChange={() => toggle(i)} />
              {/* Each member with where it applies and whether it is verified, and the group with what
                  the merged fact will be: it keeps the group's folder (a group never spans two), and
                  it is unverified if ANY member is — which relabels the members that were not. */}
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  {t("memory.consolidate.group", { kind: g.kind, n: g.items.length })}
                  <ScopeBadge project={g.project} />
                </span>
                {g.unverified && (
                  <span className="block text-xs text-warn-foreground">{t("memory.consolidate.willBeUnverified")}</span>
                )}
                {g.items.map((it) => (
                  <span key={it.id} className="mt-0.5 flex flex-wrap items-center gap-1.5 text-sm">
                    <span className="whitespace-pre-line">{it.content}</span>
                    {it.provenance === "tainted" && <Badge tone="warn">{t("memory.unverified")}</Badge>}
                    {it.source !== "chimera" && <Badge tone="muted">{it.source}</Badge>}
                  </span>
                ))}
              </span>
            </label>
          ))}
          {!preview.can_answer && <p className="text-xs text-warn-foreground">{t("memory.consolidate.noModel")}</p>}
          <div>
            <Button
              size="sm"
              disabled={chosen.size === 0 || !preview.can_answer || merge.isPending}
              onClick={() => merge.mutate([...chosen].map((i) => preview.groups[i].items.map((it) => it.id)))}
            >
              {t("memory.consolidate.apply", { n: chosen.size })}
            </Button>
          </div>
          {merge.isError && <ErrorState error={merge.error} onRetry={() => merge.reset()} />}
        </>
      )}
    </div>
  );
}

/** Export, import from Claude, and merge similar facts — every write behind a preview. */
export function MemoryTools() {
  const t = useT();
  return (
    <Panel title={t("memory.tools.title")}>
      <ExportSection />
      <ClaudeImportSection sectionTitle={sectionTitle} />
      <ConsolidateSection />
    </Panel>
  );
}
