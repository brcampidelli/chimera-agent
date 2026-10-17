import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getMarkdownAgents, listCodeProjects } from "@/lib/api";
import { Badge } from "@/components/ui/panel";
import { definitionSourceLabel } from "@/lib/definition-source";
import { useT, type TFunc } from "@/lib/i18n";
import type { MarkdownAgent } from "@/lib/types";

/**
 * The subagents a project defines in Markdown — `.chimera/agents`, the owner's `<home>/agents`, and
 * an active plugin's — listed beside the ones saved in the app, and read only.
 *
 * They could only be seen by running `chimera solve --subagents` and reading its console. This shows
 * what that run would get, including the part a file does not say: the tools are the file's list cut
 * down to what this install has and the owner's fence allows, and a project or plugin file cannot
 * choose the model. A file that was shadowed or refused is listed with the reason, because a missing
 * row and a broken file look the same otherwise.
 *
 * The Code turn has no way to run them yet — it has no subagent tool — and the screen says so
 * rather than leaving someone to look for the button.
 */
export function MarkdownAgents() {
  const t = useT();
  const [workspace, setWorkspace] = useState("");
  const projects = useQuery({ queryKey: ["code-projects"], queryFn: listCodeProjects });
  const listing = useQuery({
    queryKey: ["markdown-agents", workspace],
    queryFn: () => getMarkdownAgents(workspace),
  });
  const visible = (projects.data ?? []).filter((p) => !p.hidden);

  return (
    <section className="grid gap-2 px-3 pb-3" aria-label={t("mdAgents.title")}>
      <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
        <h2 className="text-sm font-semibold">{t("mdAgents.title")}</h2>
        <select
          className="field h-8 w-56 px-2.5 text-sm"
          aria-label={t("mdAgents.project")}
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
        >
          <option value="">{t("mdAgents.defaultProject")}</option>
          {visible.map((p) => (
            <option key={p.path} value={p.path}>
              {p.alias || p.path}
            </option>
          ))}
        </select>
      </div>
      <p className="max-w-measure text-xs text-muted-foreground">{t("mdAgents.blurb")}</p>
      {listing.data && !listing.data.runs_in_code ? (
        <p className="max-w-measure text-xs text-warn-foreground" role="note">
          {t("mdAgents.runsFromSolve")}
        </p>
      ) : null}
      {listing.isError ? (
        <p role="alert" className="text-xs text-bad-foreground">
          {listing.error instanceof Error ? listing.error.message : String(listing.error)}
        </p>
      ) : null}
      {listing.isPending ? (
        <p className="text-xs text-muted-foreground">{t("common.loading")}</p>
      ) : null}
      {listing.data?.agents.length === 0 ? (
        <p className="py-3 text-center text-xs text-muted-foreground">{t("mdAgents.empty")}</p>
      ) : null}
      <ul className="grid gap-2">
        {listing.data?.agents.map((agent) => <AgentRow key={agent.id} agent={agent} t={t} />)}
      </ul>
      {listing.data?.issues.length ? (
        <div className="grid gap-1">
          <h3 className="text-xs font-semibold">{t("mdAgents.issues")}</h3>
          <ul className="grid gap-1">
            {listing.data.issues.map((issue, i) => (
              <li key={`${issue.path}-${i}`} className="text-xs text-warn-foreground">
                <code className="font-mono">{issue.agent_id || issue.path}</code>: {issue.message}
                {issue.agent_id ? (
                  <span className="text-muted-foreground"> ({issue.path})</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function AgentRow({ agent, t }: { agent: MarkdownAgent; t: TFunc }) {
  const dropped = agent.declared_tools.filter((name) => !agent.effective_tools.includes(name));
  return (
    <li className="surface grid gap-1.5 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <code className="font-mono text-sm text-accent2">{agent.id}</code>
        <Badge tone={agent.source.startsWith("plugin:") ? "warn" : "muted"} title={agent.file}>
          {definitionSourceLabel(t, agent.source)}
        </Badge>
        {agent.model_ignored ? (
          <Badge tone="muted">{t("mdAgents.modelIgnored")}</Badge>
        ) : (
          <Badge tone="muted">{agent.model ?? t("mdAgents.modelDefault")}</Badge>
        )}
        <Badge tone="muted">{t("mdAgents.steps", { n: agent.steps })}</Badge>
      </div>
      {agent.description ? <p className="text-sm">{agent.description}</p> : null}
      <p className="text-xs text-muted-foreground">
        {agent.effective_tools.length
          ? t("mdAgents.tools", { tools: agent.effective_tools.join(", ") })
          : t("mdAgents.noTools")}
      </p>
      {dropped.length ? (
        <p className="text-xs text-muted-foreground">
          {t("mdAgents.dropped", { tools: dropped.join(", ") })}
        </p>
      ) : null}
    </li>
  );
}
