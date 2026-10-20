import { useId } from "react";
import { Brain } from "lucide-react";

import { Memory } from "@/components/Memory";
import { Profile } from "@/components/Profile";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";
import { useRoute } from "@/lib/router";

/**
 * What the agent knows.
 *
 * Memory and Profile were separate rail destinations answering one question, and fifteen icons is
 * past the point where anyone reads them — they become positions to memorise rather than words.
 * Skills now lives in Settings › Personalization; App rewrites the old address before rendering.
 *
 * Profile is new here: `/api/memory/profile` had existed with nothing reading it.
 */
export function Knowledge() {
  const t = useT();
  const id = useId();
  const { route, setParams } = useRoute();
  const tab = route.params.get("tab") === "profile" ? "profile" : "memory";
  const setTab = (next: string) => {
    const params = Object.fromEntries(route.params);
    if (next === "memory") delete params.tab;
    else params.tab = next;
    setParams(params);
  };

  const items = [
    { value: "memory", label: t("nav.memory") },
    { value: "profile", label: t("nav.profile") },
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
            {tab === "profile" && <Profile onOpenMemory={() => setTab("memory")} />}
          </TabPanel>
        </div>
      </div>
    </div>
  );
}
