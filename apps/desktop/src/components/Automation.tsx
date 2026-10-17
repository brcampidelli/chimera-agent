import { useId, useState } from "react";
import { Clock } from "lucide-react";

import { AgentRegistry } from "@/components/AgentRegistry";
import { Cron } from "@/components/Cron";
import { MarkdownAgents } from "@/components/MarkdownAgents";
import { Tasks } from "@/components/Tasks";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

export type AutomationTab = "schedule" | "tasks" | "agents";

const AUTOMATION_TABS: readonly AutomationTab[] = ["schedule", "tasks", "agents"];

/** A `?tab=` value from the URL, or the first tab for anything else — a stale link lands on the
 *  screen rather than on nothing. Same contract as `settingsTab`. */
export function automationTab(value: string | null | undefined): AutomationTab {
  return AUTOMATION_TABS.find((tab) => tab === value) ?? "schedule";
}

/**
 * Work the agent does without being asked.
 *
 * Schedule is what it will do; Tasks is what it is doing and what needs your approval. Two views of
 * the same idea, which is why they were two rail icons that people had to learn were related.
 *
 * Agents is the third: the list the board dispatches against. It sat behind the CLI while the board
 * shipped a free text box pointing at it, so the only way to learn an id was wrong was a dispatch
 * that reported "0 worked".
 */
export function Automation({
  tab: routeTab,
  onTabChange,
}: { tab?: AutomationTab; onTabChange?: (tab: AutomationTab) => void } = {}) {
  const t = useT();
  const id = useId();
  // Owned by the route when the app passes one, like Knowledge and Settings.
  const [ownTab, setOwnTab] = useState<AutomationTab>("schedule");
  const tab = onTabChange ? (routeTab ?? "schedule") : ownTab;
  const setTab = onTabChange ?? setOwnTab;

  const items = [
    { value: "schedule" as const, label: t("nav.schedule") },
    { value: "tasks" as const, label: t("nav.tasks") },
    { value: "agents" as const, label: t("nav.agentRegistry") },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 px-6 pt-6 text-accent">
        <Clock className="h-5 w-5" />
        <h1 className="text-lg font-semibold text-foreground">{t("nav.automation")}</h1>
      </div>
      <Tabs
        items={items}
        value={tab}
        onChange={setTab}
        aria-label={t("nav.automation")}
        className="px-6"
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-6">
          <TabPanel tabsId={id} value={tab}>
            {tab === "schedule" && <Cron embedded />}
            {tab === "tasks" && <Tasks embedded />}
            {/* Saved in the app first — those are the ones you edit — then the read-only Markdown
                and plugin definitions, which a file in the project (or a plugin) owns. */}
            {tab === "agents" && (
              <>
                <AgentRegistry embedded />
                <MarkdownAgents />
              </>
            )}
          </TabPanel>
        </div>
      </div>
    </div>
  );
}
