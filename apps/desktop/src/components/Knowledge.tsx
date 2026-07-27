import { useId, useState } from "react";
import { Brain } from "lucide-react";

import { Memory } from "@/components/Memory";
import { Profile } from "@/components/Profile";
import { Skills } from "@/components/Skills";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";

type Tab = "memory" | "profile" | "skills";

/**
 * What the agent knows.
 *
 * Memory, Profile and Skills were three separate rail destinations answering one question, and
 * fifteen icons is past the point where anyone reads them — they become positions to memorise
 * rather than words. Grouped, the rail carries five things and each one is a question a person
 * actually has.
 *
 * Profile is new here: `/api/memory/profile` had existed with nothing reading it.
 */
export function Knowledge() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>("memory");

  const items = [
    { value: "memory" as const, label: t("nav.memory") },
    { value: "profile" as const, label: t("nav.profile") },
    { value: "skills" as const, label: t("nav.skills") },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 px-6 pt-6 text-accent">
        <Brain className="h-5 w-5" />
        <h1 className="text-lg font-semibold text-foreground">{t("nav.knowledge")}</h1>
      </div>
      <Tabs items={items} value={tab} onChange={setTab} aria-label={t("nav.knowledge")} className="px-6" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-6 py-6">
          <TabPanel tabsId={id} value={tab}>
            {tab === "memory" && <Memory embedded />}
            {tab === "profile" && <Profile />}
            {tab === "skills" && <Skills embedded />}
          </TabPanel>
        </div>
      </div>
    </div>
  );
}
