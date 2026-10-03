import { useId, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { getStorage, pruneWorktrees, rotateLogs } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";
import type { StorageCategory } from "@/lib/types";

const WORKTREE_DIR_KEY = "CHIMERA_WORKTREE_DIR";

/** The words for each category the server reports. Spelled out rather than built from the key, so
 *  each label is a literal the translation checks can see. A key the server adds later is shown
 *  under its own name rather than dropped: an unnamed row is better than a missing size. */
const LABELS = new Map<string, string>(Object.entries({
  sessions: "settings.storage.cat.sessions",
  history: "settings.storage.cat.history",
  memory: "settings.storage.cat.memory",
  scheduler: "settings.storage.cat.scheduler",
  approvals: "settings.storage.cat.approvals",
  bundles: "settings.storage.cat.bundles",
  voice: "settings.storage.cat.voice",
  logs: "settings.storage.cat.logs",
  cache: "settings.storage.cat.cache",
  other: "settings.storage.cat.other",
  worktrees: "settings.storage.cat.worktrees",
  browsers: "settings.storage.cat.browsers",
}));

/** A size in the CHOSEN language's digits and units — `1.5 GB`, `1,5 GB`. Null stays null: the
 *  caller says "not measured", never `0 B` (the contract of `GET /api/storage`). */
export function useBytes(): (n: number) => string {
  const { lang } = useI18n();
  return useMemo(() => {
    const units = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte"] as const;
    const formats = units.map(
      (unit, i) =>
        new Intl.NumberFormat(lang, {
          style: "unit",
          unit,
          unitDisplay: "short",
          maximumFractionDigits: i === 0 ? 0 : 1,
        }),
    );
    return (n: number) => {
      let value = n;
      let i = 0;
      while (value >= 1024 && i < units.length - 1) {
        value /= 1024;
        i += 1;
      }
      return formats[i].format(value);
    };
  }, [lang]);
}

/**
 * What Chimera keeps on this computer, by kind, and the two things that may safely be let go
 * (study 29, P5.3).
 *
 * A row reads "not measured" when the server could not count it — an unreadable folder, a count
 * that ran out of time, a location unknown here — and never 0: a partial count shown as a size is
 * believed as one. The two actions each need a second press that says what will and will not be
 * touched; the server refuses either without `confirm: true` as well, so a stray call does nothing.
 * Conversations, memory, history and approvals are only ever measured here.
 *
 * The worktree folder is the one setting on the card. Empty is the system temp folder — what it
 * always was — and the line under it says where the NEXT worktree will actually go, because the
 * server ignores a value that breaks its rules (relative, or inside the project) and the owner
 * should see that rather than believe their disk is being spared.
 */
export function StorageCard({
  worktreeDir,
  onSave,
}: {
  worktreeDir: string;
  onSave: (updates: Record<string, string>) => void;
}) {
  const t = useT();
  const bytes = useBytes();
  const headingId = useId();
  const fieldId = useId();
  const qc = useQueryClient();
  const report = useQuery({ queryKey: ["storage"], queryFn: () => getStorage(), retry: false });
  const [confirming, setConfirming] = useState<"prune" | "rotate" | null>(null);
  const [dir, setDir] = useState(worktreeDir);
  const refresh = () => qc.invalidateQueries({ queryKey: ["storage"] });
  // Wrapped, not passed by reference, for the reason `FoldersCard` gives: this renders inside
  // Settings, and a client function that is missing must fail this card's action, not the screen.
  const prune = useMutation({ mutationFn: () => pruneWorktrees(), onSuccess: refresh });
  const rotate = useMutation({ mutationFn: () => rotateLogs(), onSuccess: refresh });

  const size = (row: StorageCategory) =>
    row.bytes === null ? t("settings.storage.notMeasured") : bytes(row.bytes);
  const label = (key: string) => {
    const name = LABELS.get(key);
    return name ? t(name) : key;
  };

  const data = report.data;
  const worktrees = data?.worktrees ?? [];
  const orphans = worktrees.filter((w) => w.state === "orphan");
  const orphanBytes = orphans.reduce((sum, w) => sum + (w.bytes ?? 0), 0);
  const kept = worktrees.filter((w) => w.state === "kept").length;
  const live = worktrees.filter((w) => w.state === "live").length;
  const logs = data?.categories.find((c) => c.key === "logs");

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.storage")}
      </h2>
      <div className="divide-y divide-hairline">
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.storage.intro")}</p>
        {report.isError && (
          <p role="status" className="px-4 py-2.5 text-xs text-bad-foreground">
            {t("settings.storage.unavailable")}
          </p>
        )}
        {data && (
          <dl className="space-y-1 px-4 py-3 text-sm">
            {data.categories.map((row) => (
              <div key={row.key} className="flex items-baseline justify-between gap-3">
                <dt className="min-w-0 truncate" title={row.paths.join("\n")}>
                  {label(row.key)}
                </dt>
                <dd
                  className={
                    row.bytes === null
                      ? "shrink-0 text-xs text-warn-foreground"
                      : "shrink-0 font-mono text-xs"
                  }
                  title={row.note || undefined}
                >
                  {size(row)}
                </dd>
              </div>
            ))}
          </dl>
        )}
        {data &&
          data.disks.map((disk) => (
            <p key={disk.path} className="px-4 py-2.5 text-xs text-muted-foreground">
              {disk.free === null || disk.total === null
                ? t("settings.storage.diskUnknown", { path: disk.path })
                : t("settings.storage.free", {
                    free: bytes(disk.free),
                    total: bytes(disk.total),
                    path: disk.path,
                  })}
            </p>
          ))}

        <div className="space-y-2 px-4 py-3">
          <label htmlFor={fieldId} className="block text-sm font-medium">
            {t("settings.row.worktreeDir")}
          </label>
          <div className="text-xs text-muted-foreground">{t("settings.hint.worktreeDir")}</div>
          <div className="flex items-center gap-2">
            <input
              id={fieldId}
              className="field h-8 min-w-0 flex-1 px-2.5 font-mono text-xs"
              value={dir}
              placeholder={t("settings.storage.worktreeDirPlaceholder")}
              onChange={(e) => setDir(e.target.value)}
            />
            <Button
              size="sm"
              disabled={dir === worktreeDir}
              onClick={() => onSave({ [WORKTREE_DIR_KEY]: dir.trim() })}
            >
              {t("common.save")}
            </Button>
          </div>
          {data && (
            <div className="break-all text-xs text-muted-foreground">
              {t("settings.storage.worktreeNow", { path: data.worktree_dir })}
            </div>
          )}
        </div>

        {data && (
          <div className="space-y-2 px-4 py-3">
            <div className="text-xs text-muted-foreground">
              {t("settings.storage.worktreeCounts", {
                count: worktrees.length,
                orphans: orphans.length,
                live,
                kept,
              })}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={orphans.length === 0 || prune.isPending}
                onClick={() => setConfirming("prune")}
              >
                {t("settings.storage.prune")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!logs?.bytes || rotate.isPending}
                onClick={() => setConfirming("rotate")}
              >
                {t("settings.storage.rotate")}
              </Button>
            </div>
            {confirming && (
              <div className="flex flex-col gap-2">
                <p className="text-xs text-warn-foreground">
                  {confirming === "prune"
                    ? t("settings.storage.pruneConfirm", {
                        count: orphans.length,
                        size: bytes(orphanBytes),
                      })
                    : t("settings.storage.rotateConfirm", {
                        files: data.rotatable_logs.join(", "),
                      })}
                </p>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      if (confirming === "prune") prune.mutate();
                      else rotate.mutate();
                      setConfirming(null);
                    }}
                  >
                    {t("settings.storage.confirm")}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                    {t("common.cancel")}
                  </Button>
                </div>
              </div>
            )}
            {prune.data && (
              <p role="status" className="text-xs text-muted-foreground">
                {t("settings.storage.pruned", {
                  removed: prune.data.removed,
                  size: bytes(prune.data.bytes_freed),
                  kept: prune.data.kept,
                  live: prune.data.live,
                })}
                {prune.data.failed > 0 &&
                  ` ${t("settings.storage.failed", { failed: prune.data.failed })}`}
              </p>
            )}
            {rotate.data && (
              <p role="status" className="text-xs text-muted-foreground">
                {t("settings.storage.rotated", {
                  rotated: rotate.data.rotated,
                  size: bytes(rotate.data.bytes_freed),
                })}
                {rotate.data.failed > 0 &&
                  ` ${t("settings.storage.failed", { failed: rotate.data.failed })}`}
              </p>
            )}
            {(prune.isError || rotate.isError) && (
              <p role="status" className="text-xs text-bad-foreground">
                {t("settings.storage.actionFailed")}
              </p>
            )}
          </div>
        )}
        <p className="px-4 py-2.5 text-xs text-muted-foreground">{t("settings.storage.never")}</p>
      </div>
    </section>
  );
}
