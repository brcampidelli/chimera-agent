import { useId, useState } from "react";
import { Clock } from "lucide-react";

import { Cron } from "@/components/Cron";
import { Tasks } from "@/components/Tasks";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

type Tab = "schedule" | "tasks";

/**
 * Work the agent does without being asked.
 *
 * Schedule is what it will do; Tasks is what it is doing and what needs your approval. Two views of
 * the same idea, which is why they were two rail icons that people had to learn were related.
 */
export function Automation() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>("schedule");

  const items = [
    { value: "schedule" as const, label: t("nav.schedule") },
    { value: "tasks" as const, label: t("nav.tasks") },
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
          </TabPanel>
        </div>
      </div>
    </div>
  );
}
