import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Wrench, Search } from "lucide-react";
import { getTools } from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT, type TFunc } from "@/lib/i18n";
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

/** One tool row: name (mono, bold), description (muted), capability tags + params. */
function ToolRow({ tool, t }: { tool: ToolInfo; t: TFunc }) {
  return (
    <div className="flex flex-col gap-1.5 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs font-bold text-foreground">{tool.name}</span>
        {tool.tags.map((tag) => (
          <Badge key={tag} tone={TAG_TONE[tag] ?? "muted"}>
            {t(`tools.tag.${tag === "side-effect" ? "sideEffect" : tag}`)}
          </Badge>
        ))}
        {tool.untrusted_output && <Badge tone="warn">{t("tools.tag.untrusted")}</Badge>}
      </div>
      {tool.description && (
        <div className="text-[13px] leading-snug text-muted-foreground">{tool.description}</div>
      )}
      {tool.params.length > 0 ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">
            {t("tools.params", { n: tool.params.length })}
          </span>
          {tool.params.map((p) => (
            <span
              key={p}
              className="rounded-chip bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground ring-1 ring-white/5"
            >
              {p}
            </span>
          ))}
        </div>
      ) : (
        <span className="text-[11px] text-muted-foreground">{t("tools.noParams")}</span>
      )}
    </div>
  );
}

export function Tools() {
  const t = useT();
  const q = useQuery({ queryKey: ["tools"], queryFn: getTools });
  const [query, setQuery] = useState("");

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
      <Screen title={t("tools.title")} icon={<Wrench className="h-5 w-5" />}>
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
          filtered.map((tool) => <ToolRow key={tool.name} tool={tool} t={t} />)
        )}
      </Panel>
      <p className="px-1 text-[11px] text-muted-foreground">{t("tools.note")}</p>
    </Screen>
  );
}
