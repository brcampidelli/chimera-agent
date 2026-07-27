import { useId, useState } from "react";
import { ListChecks } from "lucide-react";

import { Agents } from "@/components/Agents";
import { Runs } from "@/components/Runs";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

type Tab = "run" | "agents";

/**
 * The agent working on its own.
 *
 * Runs is one task with verify-or-revert; Agents is several of the same in parallel git worktrees.
 * They were separate destinations that both started the same kind of run — and both shipped their
 * own copy of the launcher, which is now shared code rather than shared screen space.
 */
export function Work() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>("run");

  const items = [
    { value: "run" as const, label: t("nav.runs") },
    { value: "agents" as const, label: t("nav.agents") },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 px-6 pt-6 text-accent">
        <ListChecks className="h-5 w-5" />
        <h1 className="text-lg font-semibold text-foreground">{t("nav.work")}</h1>
      </div>
      <Tabs items={items} value={tab} onChange={setTab} aria-label={t("nav.work")} className="px-6" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <TabPanel tabsId={id} value={tab}>
          {/* Agents lays itself out full-bleed; Runs wants the centred column. Each keeps what it
              needs rather than being forced into one shape. */}
          {tab === "run" ? (
            <div className="mx-auto max-w-3xl px-6 py-6">
              <Runs embedded />
            </div>
          ) : (
            <Agents />
          )}
        </TabPanel>
      </div>
    </div>
  );
}
