import { useQuery } from "@tanstack/react-query";

import { getResources } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * What this machine is spending while the agent works.
 *
 * The whole design of this panel is the sentence it refuses to say. Every number arrives nullable
 * and an absent one renders as "unavailable" — never as 0%, never as an empty bar. Zero VRAM reads
 * as "the GPU is idle" and zero CPU reads as "nothing is running"; both are claims about hardware,
 * and on an AMD or Apple machine — where `nvidia-smi` does not exist — they are claims we have no
 * way to make and that would be believed.
 *
 * Polled rather than streamed. A resource readout is decoration next to the conversation, and a
 * second SSE channel for it would be a second thing that can fail while the agent is working.
 */

/** A bar with a label. Renders the missing case as prose, deliberately: an empty bar looks like an
 *  idle machine, and the whole point is that we do not know. */
function Reading({
  label,
  value,
  detail,
  unavailable,
}: {
  label: string;
  /** 0..100, or null when unknown. */
  value: number | null;
  detail?: string;
  unavailable: string;
}) {
  return (
    <div className="mb-2">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className={value === null ? "text-muted-foreground" : "text-foreground"}>
          {value === null ? unavailable : `${Math.round(value)}%`}
        </span>
      </div>
      {value === null ? null : (
        <div className="mt-1 h-1 overflow-hidden rounded-full bg-surface-2">
          <div
            className="h-full bg-accent transition-all duration-2 ease-out"
            style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
          />
        </div>
      )}
      {detail ? <p className="mt-0.5 text-xs text-muted-foreground">{detail}</p> : null}
    </div>
  );
}

export function MachinePanel() {
  const t = useT();
  const machine = useQuery({
    queryKey: ["resources"],
    queryFn: getResources,
    // Every four seconds: often enough to watch a run heat the machine up, rare enough that the
    // panel is not itself a load. `staleTime: 0` because a cached reading of a live number is not
    // a reading of anything.
    refetchInterval: 4000,
    staleTime: 0,
  });

  const data = machine.data;
  if (!data) return null;

  const unavailable = t("machine.unavailable");
  const memoryDetail =
    data.memory.used_mb != null && data.memory.total_mb != null
      ? t("machine.memoryDetail", {
          used: Math.round(data.memory.used_mb / 1024),
          total: Math.round(data.memory.total_mb / 1024),
        })
      : undefined;

  return (
    <div>
      <Reading
        label={t("machine.cpu")}
        value={data.cpu_percent ?? null}
        detail={data.cpu_count ? t("machine.cores", { n: data.cpu_count }) : undefined}
        unavailable={unavailable}
      />
      <Reading
        label={t("machine.memory")}
        value={data.memory.percent ?? null}
        detail={memoryDetail}
        unavailable={unavailable}
      />
      {data.gpus.map((gpu) => (
        <Reading
          key={gpu.name}
          label={gpu.name}
          value={
            gpu.vram_used_mb != null && gpu.vram_total_mb
              ? (gpu.vram_used_mb / gpu.vram_total_mb) * 100
              : null
          }
          detail={
            gpu.vram_used_mb != null && gpu.vram_total_mb != null
              ? t("machine.vramDetail", {
                  used: (gpu.vram_used_mb / 1024).toFixed(1),
                  total: (gpu.vram_total_mb / 1024).toFixed(1),
                })
              : unavailable
          }
          unavailable={unavailable}
        />
      ))}
      {/* The server's own words about what it could not read, and what to install. Rendering them
          is what turns a gap from a mystery into an instruction. */}
      {data.notes.map((note) => (
        <p key={note} className="mt-1 text-xs text-muted-foreground">
          {note}
        </p>
      ))}
      {data.process_mb != null ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("machine.process", { mb: data.process_mb })}
        </p>
      ) : null}
    </div>
  );
}
