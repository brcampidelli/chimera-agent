import { useState } from "react";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import type { CrewWorkerInput } from "@/lib/api";

/**
 * Assembling a crew: who tries, and what decides which attempt lands.
 *
 * The check is the first field rather than the last, and that is not a layout preference. Every
 * worker attacks the SAME task, so they tend to edit the same files — and the merge rule is
 * one-file-one-owner, meaning that when two of them succeed on the same file, NEITHER lands.
 * Without a check that eliminates the losers, a crew usually produces nothing at all. Putting it
 * last, as an optional extra, would be arranging the screen so the common outcome is empty.
 */
const DEFAULT_WORKERS: CrewWorkerInput[] = [
  { name: "conservador", instruction: "" },
  { name: "direto", instruction: "" },
];

export function CrewForm({
  onRun,
  running,
}: {
  onRun: (workers: CrewWorkerInput[], verify: string) => void;
  running: boolean;
}) {
  const t = useT();
  const [verify, setVerify] = useState("");
  const [workers, setWorkers] = useState<CrewWorkerInput[]>(DEFAULT_WORKERS);

  const named = workers.filter((w) => w.name.trim());
  const duplicated =
    new Set(named.map((w) => w.name.trim())).size !== named.length;

  function edit(index: number, patch: Partial<CrewWorkerInput>) {
    setWorkers((prev) => prev.map((w, i) => (i === index ? { ...w, ...patch } : w)));
  }

  return (
    <div className="space-y-4 rounded-card border border-hairline bg-surface-2/40 p-4">
      <div>
        <label
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
          htmlFor="crew-verify"
        >
          {t("crew.verify.label")}
        </label>
        <input
          id="crew-verify"
          value={verify}
          onChange={(event) => setVerify(event.target.value)}
          placeholder={t("crew.verify.placeholder")}
          className="mt-1 w-full rounded-card border border-hairline bg-surface-2/40 p-2 font-mono text-xs text-foreground placeholder:text-muted-foreground"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          {verify.trim() ? t("crew.verify.why") : t("crew.verify.missing")}
        </p>
      </div>

      <div className="space-y-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("crew.workers.label")}
        </span>
        {workers.map((worker, index) => (
          <div key={index} className="flex gap-2">
            <input
              value={worker.name}
              onChange={(event) => edit(index, { name: event.target.value })}
              placeholder={t("crew.workers.name")}
              aria-label={t("crew.workers.nameOf", { n: index + 1 })}
              className="w-32 shrink-0 rounded-card border border-hairline bg-surface-2/40 p-2 text-xs text-foreground placeholder:text-muted-foreground"
            />
            <input
              value={worker.instruction}
              onChange={(event) => edit(index, { instruction: event.target.value })}
              placeholder={t("crew.workers.instruction")}
              aria-label={t("crew.workers.instructionOf", { n: index + 1 })}
              className="min-w-0 flex-1 rounded-card border border-hairline bg-surface-2/40 p-2 text-xs text-foreground placeholder:text-muted-foreground"
            />
            {workers.length > 1 ? (
              <button
                type="button"
                aria-label={t("crew.workers.remove", { n: index + 1 })}
                onClick={() => setWorkers((prev) => prev.filter((_, i) => i !== index))}
                className="shrink-0 text-muted-foreground hover:text-foreground"
              >
                <X className="h-4 w-4" />
              </button>
            ) : null}
          </div>
        ))}
        {workers.length < 8 ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setWorkers((prev) => [...prev, { name: "", instruction: "" }])}
          >
            <Plus className="h-4 w-4" /> {t("crew.workers.add")}
          </Button>
        ) : null}
      </div>

      {duplicated ? (
        // The name is how every frame is routed, so two of the same would put two workers'
        // results on one card. The server refuses it; saying so here saves the round trip.
        <p className="text-xs text-bad">{t("crew.workers.duplicate")}</p>
      ) : null}

      <Button
        size="sm"
        disabled={running || named.length < 2 || duplicated}
        onClick={() => onRun(named.map((w) => ({ ...w, name: w.name.trim() })), verify.trim())}
      >
        {t("crew.run")}
      </Button>
      {named.length < 2 ? (
        <p className="text-xs text-muted-foreground">{t("crew.needTwo")}</p>
      ) : null}
    </div>
  );
}
