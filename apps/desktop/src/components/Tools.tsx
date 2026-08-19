import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Wrench, Search } from "lucide-react";
import { getConfig, getTools, patchConfig } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { Switch } from "@/components/ui/switch";
import { ErrorState } from "@/components/ui/async";
import { useT, type TFunc } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { ToolInfo } from "@/lib/types";

/** Tag → badge tone. Higher-blast-radius capabilities read louder: write/exec (bad), network/
 *  side-effect (warn), read (muted). Kept in sync with the governance-derived tag set. */
const TAG_TONE: Record<string, "muted" | "warn" | "bad"> = {
  read: "muted",
  network: "warn",
  "side-effect": "warn",
  write: "bad",
  exec: "bad",
};

/** One tool row: name (mono, bold), description (muted), capability tags + params, and the switch.
 *
 * The switch is the point. This screen has always been an honest inventory of what the agent can do
 * and has never let anyone change it — so "what may my agent do" was answerable only by hand-editing
 * CHIMERA_TOOL_DENYLIST in a file, and until this release that variable did not even reach the app.
 */
function ToolRow({
  tool,
  denied,
  onToggle,
  t,
}: {
  tool: ToolInfo;
  denied: boolean;
  onToggle: (allowed: boolean) => void;
  t: TFunc;
}) {
  return (
    <div className="flex flex-col gap-1.5 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Switch checked={!denied} onChange={onToggle} label={tool.name} />
        <span
          className={cn(
            "font-mono text-xs font-bold",
            denied ? "text-muted-foreground line-through" : "text-foreground",
          )}
        >
          {tool.name}
        </span>
        {tool.tags.map((tag) => (
          <Badge key={tag} tone={TAG_TONE[tag] ?? "muted"}>
            {t(`tools.tag.${tag === "side-effect" ? "sideEffect" : tag}`)}
          </Badge>
        ))}
        {tool.untrusted_output && <Badge tone="warn">{t("tools.tag.untrusted")}</Badge>}
      </div>
      {tool.description && (
        <div className="text-sm leading-snug text-muted-foreground">{tool.description}</div>
      )}
      {tool.params.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted-foreground">
            {t("tools.params", { n: tool.params.length })}
          </span>
          {tool.params.map((p) => (
            <span
              key={p}
              className="rounded-chip bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-muted-foreground ring-1 ring-hairline"
            >
              {p}
            </span>
          ))}
        </div>
      ) : (
        <span className="text-xs text-muted-foreground">{t("tools.noParams")}</span>
      )}
    </div>
  );
}

export function Tools({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["tools"], queryFn: getTools });
  const cfg = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const [query, setQuery] = useState("");

  const denied = useMemo(
    () => new Set(cfg.data?.autonomy.denied_tools ?? []),
    [cfg.data],
  );
  const save = useMutation({
    mutationFn: (names: string[]) =>
      patchConfig({ CHIMERA_TOOL_DENYLIST: names.join(",") }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });
  // Denied names are kept even for tools this build does not have: a denylist naming a tool that
  // arrives with a later version should still deny it, and silently dropping unknown names would
  // turn "I switched this off" into "I switched this off until an upgrade".
  const toggle = (name: string, allowed: boolean) => {
    const next = new Set(denied);
    if (allowed) next.delete(name);
    else next.add(name);
    save.mutate([...next].sort());
  };

  const all = q.data?.tools ?? [];
  const term = query.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      term
        ? all.filter(
            (tool) =>
              tool.name.toLowerCase().includes(term) ||
              tool.description.toLowerCase().includes(term),
          )
        : all,
    [all, term],
  );

  if (q.isError) {
    return (
      <Screen title={t("tools.title")} icon={<Wrench className="h-5 w-5" />} embedded={embedded}>
        <Panel>
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        </Panel>
      </Screen>
    );
  }
  if (q.isLoading || !q.data) {
    return (
      <Screen title={t("tools.title")} icon={<Wrench className="h-5 w-5" />}>
        <Panel>
          <Spinner />
        </Panel>
      </Screen>
    );
  }

  return (
    <Screen title={t("tools.title")} icon={<Wrench className="h-5 w-5" />}>
      <Panel
        title={t("tools.count", { n: q.data.count })}
        action={
          <div className="flex items-center gap-1">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <input
              className="field h-7 w-40 px-2 text-xs"
              placeholder={t("common.search")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
        }
      >
        {q.data.count === 0 ? (
          <EmptyState text={t("tools.empty")} />
        ) : filtered.length === 0 ? (
          <EmptyState text={t("tools.emptySearch")} />
        ) : (
          filtered.map((tool) => (
            <ToolRow
              key={tool.name}
              tool={tool}
              denied={denied.has(tool.name)}
              onToggle={(allowed) => toggle(tool.name, allowed)}
              t={t}
            />
          ))
        )}
      </Panel>
      <p className="px-1 text-xs text-muted-foreground">{t("tools.note")}</p>
      {/* Why every row above is English even when the app is not. A tool's description and its
          parameter names are not our copy about the agent — they ARE the function schema sent to
          the model, so a translated copy on this screen would describe an agent that does not
          exist, on the one screen whose whole job is to be an honest inventory. Saying so is
          cheaper than either lying or leaving it unexplained. */}
      <p className="px-1 text-xs text-muted-foreground">{t("tools.langNote")}</p>
    </Screen>
  );
}
