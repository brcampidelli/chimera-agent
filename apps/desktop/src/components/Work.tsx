import { useId, useState } from "react";
import { ListChecks } from "lucide-react";

import { Orchestration } from "@/components/orchestration/Orchestration";
import { Runs } from "@/components/Runs";
import { GitPanel } from "@/components/code/GitPanel";
import { WorthPanel } from "@/components/code/WorthPanel";
import { Tabs, TabPanel } from "@/components/ui/tabs";
import { useT } from "@/lib/i18n";
import { useRoute } from "@/lib/router";
import { readWorkspace } from "@/lib/workspace";

type Tab = "run" | "git" | "worth" | "orchestration";

const TABS = ["run", "git", "worth", "orchestration"] as const;

/** The tab named in the URL, or "run".
 *
 *  Read straight from the hash rather than through `useRoute` so the FIRST render already has it: a
 *  deep link that resolved one render late would show the Runs tab and then jump, which reads as a
 *  bug even though it settles correctly.
 *
 *  Checked against the whole list. It used to recognise `orchestration` and nothing else, so
 *  `choose` wrote `?tab=git` into the URL and reopening that URL landed on Runs — the address bar
 *  said one thing and the screen showed another, which is worse than having no deep link at all. */
function readTab(): Tab {
  const query = window.location.hash.split("?")[1] ?? "";
  const named = new URLSearchParams(query).get("tab") ?? "";
  return (TABS as readonly string[]).includes(named) ? (named as Tab) : "run";
}

/**
 * The agent working on its own.
 *
 * Runs is one task with verify-or-revert. Several of the same, in parallel git worktrees, used to be
 * the tab beside it — a destination chosen before anyone knew whether the work was parallel. It is
 * reached from the conversation now, by asking for several things and confirming the split, so the
 * only remaining question is asked before the worktrees exist rather than reported after.
 *
 * Git and "was it worth it?" moved here from the Code screen. Both are about work that already
 * happened — reviewing the diff a run produced, and asking whether the expensive profile earned its
 * cost — which is this screen's subject, not the subject of a screen you use to write code. On Code
 * they were two of five panels sharing one column, and the arithmetic did not work: the panels that
 * could not shrink took every pixel and the conversation was laid out at zero height.
 *
 * Maturity would have been the other candidate for the cost panel and was rejected on a fact rather
 * than a preference: `App.tsx` renders it only under `import.meta.env.DEV`, so a shipped build would
 * have hidden the one panel that answers whether a profile paid for itself.
 */
export function Work() {
  const t = useT();
  const id = useId();
  const [tab, setTab] = useState<Tab>(readTab);
  // Only to leave: the fallback note on a write-shaped task offers the screen that IS for writing
  // work, and a suggestion you have to act on yourself is a suggestion with a step missing.
  const { navigate, setParams } = useRoute();
  // Read on mount, and this screen remounts on every navigation, so choosing a root in Code and
  // coming back here shows the new one. Deliberately not lifted into a provider: the value already
  // survives in storage, and a provider would be a second source of truth for the same string.
  const [workspace] = useState(readWorkspace);

  const items = [
    { value: "run" as const, label: t("nav.runs") },
    { value: "git" as const, label: t("code.git.title") },
    { value: "worth" as const, label: t("code.worth.title") },
    { value: "orchestration" as const, label: t("nav.orchestration") },
  ];

  // `setParams` and not `navigate`: switching a tab replaces rather than pushes, so the back
  // button still leaves this screen instead of walking back through four tabs first.
  function choose(next: Tab) {
    setTab(next);
    setParams(next === "run" ? {} : { tab: next });
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
          {tab === "run" ? (
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
          ) : tab === "worth" ? (
            <div className="mx-auto max-w-5xl px-6 py-4">
              <WorthPanel workspace={workspace} />
            </div>
          ) : (
            <div className="mx-auto max-w-5xl px-6 py-4">
              <Orchestration workspace={workspace} onOpenCode={() => navigate("code")} />
            </div>
          )}
        </TabPanel>
      </div>
    </div>
  );
}
