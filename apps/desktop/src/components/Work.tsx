import { useId, useState } from "react";
import { ListChecks } from "lucide-react";

import { Runs } from "@/components/Runs";
import { GitPanel } from "@/components/code/GitPanel";
import { WorthPanel } from "@/components/code/WorthPanel";
import { TaskConsole } from "@/components/work/TaskConsole";
import { readMode, type WorkMode } from "@/components/work/modes";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";
import { useRoute } from "@/lib/router";
import { readWorkspace } from "@/lib/workspace";

type Tab = "task" | "runs" | "git" | "worth";

/** The tab names the URL understands.
 *
 *  Exported so the deep-link test iterates THIS list instead of a copy of it. The union type and
 *  this array are two declarations of one fact and the typechecker cannot relate them: adding the
 *  fifth tab left `?tab=lifecycle` falling back to Runs, silently, with everything compiling.
 */
export const TABS = ["task", "runs", "git", "worth"] as const;

/** The tab named in the URL, or "task".
 *
 *  Read straight from the hash rather than through `useRoute` so the FIRST render already has it: a
 *  deep link that resolved one render late would show the first tab and then jump, which reads as a
 *  bug even though it settles correctly.
 *
 *  Checked against the whole list. It used to recognise `orchestration` and nothing else, so
 *  `choose` wrote `?tab=git` into the URL and reopening that URL landed on Runs — the address bar
 *  said one thing and the screen showed another, which is worse than having no deep link at all. */
function readTab(): Tab {
  const query = window.location.hash.split("?")[1] ?? "";
  const named = new URLSearchParams(query).get("tab") ?? "";
  return (TABS as readonly string[]).includes(named) ? (named as Tab) : "task";
}

/**
 * The agent working on its own.
 *
 * There were four ways to hand it a task and four forms to do it on — a run, a lifecycle, a
 * fan-out, and the crew buried inside the fan-out. Each asked for the task again, so the choice of
 * how to run it came before the task existed, and changing your mind cost retyping. They are one
 * screen now, with the task typed once and buttons for what happens to it. Several agents at once
 * made the same move earlier and for the same reason, written down in `Code.tsx`: it was
 * "a destination chosen before anyone knew whether the work was parallel".
 *
 * What is left beside it is what is not a way of starting work: the archive of what the runs did,
 * the diff they produced, and whether the expensive profile earned its cost. Git and "was it worth
 * it?" moved here from the Code screen because both are about work that already happened, which is
 * this screen's subject and not the subject of a screen you use to write code.
 *
 * Maturity would have been the other candidate for the cost panel and was rejected on a fact rather
 * than a preference: `App.tsx` renders it only under `import.meta.env.DEV`, so a shipped build would
 * have hidden the one panel that answers whether a profile paid for itself.
 */
export function Work() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>(readTab);
  // Read once, for the same reason as the tab. The console owns it from there on — it can change
  // its own mode, and the fallback note does exactly that.
  const [mode, setMode] = useState<WorkMode>(() => readMode(window.location.hash));
  // Only to leave: the fallback note on a write-shaped task offers the screen that IS for writing
  // work, and a suggestion you have to act on yourself is a suggestion with a step missing.
  const { navigate, setParams } = useRoute();
  // Read on mount, and this screen remounts on every navigation, so choosing a root in Code and
  // coming back here shows the new one. Deliberately not lifted into a provider: the value already
  // survives in storage, and a provider would be a second source of truth for the same string.
  const [workspace] = useState(readWorkspace);

  const items = [
    { value: "task" as const, label: t("nav.task") },
    { value: "runs" as const, label: t("nav.runs") },
    { value: "git" as const, label: t("code.git.title") },
    { value: "worth" as const, label: t("code.worth.title") },
  ];

  // `setParams` REPLACES the query, so one function emits the WHOLE of it. Two callers each
  // writing their own key would take turns dropping the other's — and the loss is invisible until
  // somebody reopens the URL and lands on a screen the address bar disagrees with.
  function write(nextTab: Tab, nextMode: WorkMode) {
    const params: Record<string, string> = {};
    if (nextTab !== "task") params.tab = nextTab;
    // Only when it is not the default and only on the tab it belongs to — a `?mode=` left behind
    // on the git tab describes a control that is not on screen.
    if (nextTab === "task" && nextMode !== "single") params.mode = nextMode;
    // Replaces rather than pushes, so the back button still leaves this screen instead of walking
    // back through four tabs first.
    setParams(params);
  }

  function choose(next: Tab) {
    setTab(next);
    write(next, mode);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 px-6 pt-6 text-accent">
        <ListChecks className="h-5 w-5" />
        <h1 className="text-lg font-semibold text-foreground">{t("nav.work")}</h1>
      </div>
      <Tabs items={items} value={tab} onChange={choose} aria-label={t("nav.work")} className="px-6" />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <TabPanel tabsId={id} value={tab}>
          {tab === "task" ? (
            <div className="mx-auto max-w-5xl px-6 py-4">
              <TaskConsole
                workspace={workspace}
                initialMode={mode}
                onMode={(next) => {
                  setMode(next);
                  write(tab, next);
                }}
                onOpenCode={() => navigate("code")}
              />
            </div>
          ) : tab === "runs" ? (
            <div className="mx-auto max-w-3xl px-6 py-6">
              <Runs embedded workspace={workspace} />
            </div>
          ) : tab === "git" ? (
            <div className="mx-auto max-w-5xl px-6 py-4">
              {/* Which folder this is the git of. The root is chosen on the Code screen, so without
                  saying so here the panel would be "git of somewhere" — and the answer to "why is
                  this empty?" would be invisible from the screen showing the emptiness. */}
              {/* Only when there IS one. The placeholder used to render here when no project was
                  chosen, so a fresh install showed "folder path (optional — defaults to the app's
                  workspace)" in monospace, where a path goes — a hint dressed up as a fact. */}
              {workspace ? (
                <p className="px-4 pb-2 font-mono text-xs text-muted-foreground">{workspace}</p>
              ) : (
                <p className="px-4 pb-2 text-xs text-muted-foreground">{t("code.sessions.defaultProject")}</p>
              )}
              <GitPanel workspace={workspace} />
            </div>
          ) : (
            <div className="mx-auto max-w-5xl px-6 py-4">
              <WorthPanel workspace={workspace} />
            </div>
          )}
        </TabPanel>
      </div>
    </div>
  );
}
