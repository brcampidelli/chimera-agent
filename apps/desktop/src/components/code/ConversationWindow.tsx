import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AgentBatchSlot, useStartAgentBatch } from "@/components/code/AgentBatchSlot";
import { ComposerSettings } from "@/components/code/ComposerSettings";
import { Conversation } from "@/components/code/Conversation";
import { EffortPicker } from "@/components/code/EffortPicker";
import { ModelPicker } from "@/components/code/ModelPicker";
import { PostureNote } from "@/components/code/PostureNote";
import { ProviderPicker } from "@/components/code/ProviderPicker";
import { StylePicker, styleLabel } from "@/components/code/StylePicker";
import { AgentBatchProvider } from "@/lib/agent-batch";
import { AgentProvider } from "@/lib/agent-context";
import { getCodeSession, getConfig, type Approval, type Profile, type Reach } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { LayoutDnd } from "@/components/shell/Dock";
import { LayoutProvider } from "@/lib/layout/context";
import { shellGranted } from "@/lib/project-shell";
import { loadProjects } from "@/lib/projects";
import { RunSessionProvider, useRunSession } from "@/lib/run-session";
import type { OutputStyle } from "@/lib/types";

/**
 * One conversation in a window of its own, so two can be worked side by side.
 *
 * The last item of the review of several conversations at once (2026-09-30). The server already runs
 * turns of different conversations at the same time, stops each on its own and keeps them apart
 * (R1–R18); what the screen had was one centre column. This is the same page, asked by
 * `?conversation=<id>` to draw that one conversation, the way `?float=` draws one panel.
 *
 * It reads the person's layout (how they like cards drawn) and writes none of it: the main window
 * owns that. Its runs are its own, held in its own session, and the conversation follows turns
 * started anywhere else, in the main window included, the way it follows a guest's.
 */
export function ConversationWindow({ sessionId }: { sessionId: string }) {
  return (
    <LayoutProvider persist={false}>
      <AgentProvider>
        <RunSessionProvider>
          <AgentBatchProvider>
            <ConversationWindowBody sessionId={sessionId} />
          </AgentBatchProvider>
        </RunSessionProvider>
      </AgentProvider>
    </LayoutProvider>
  );
}

function ConversationWindowBody({ sessionId }: { sessionId: string }) {
  const t = useT();
  const session = useQuery({ queryKey: ["code-session", sessionId], queryFn: () => getCodeSession(sessionId) });
  const cfg = useQuery({ queryKey: ["config"], queryFn: getConfig });
  // The server's record of which folders may run commands — the one the turn is held to.
  const projects = useQuery({ queryKey: ["code-projects"], queryFn: loadProjects });
  const run = useRunSession();
  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  // Picked here, so kept with the conversation (`model_pinned`) — see the Code screen's own.
  const [modelPinned, setModelPinned] = useState(false);
  const pickModel = (slug: string) => {
    setModel(slug);
    setModelPinned(true);
  };
  // The conversation's style, as the main window keeps it: the default until the stored conversation
  // says its last turn ran under another, so opening it here does not quietly change how it is written.
  const [style, setStyle] = useState<OutputStyle>("default");
  const startBatch = useStartAgentBatch();
  const workspace = session.data?.workspace ?? "";
  const project = workspace.split(/[\\/]/).filter(Boolean).pop() ?? t("approvals.defaultProject");

  useEffect(() => {
    document.title = `${project} · Chimera`;
  }, [project]);

  if (session.isPending) {
    return <main className="flex h-screen items-center justify-center bg-background text-sm text-muted-foreground">…</main>;
  }
  if (session.isError) {
    return (
      <main className="flex h-screen items-center justify-center bg-background px-6 text-sm text-foreground">
        {t("code.window.unreadable")}
      </main>
    );
  }

  // The same posture the Code screen sends for this project, read the same way: the owner's standing
  // choice, raised to shell for a project granted it and never lowered.
  const configured = (cfg.data?.autonomy.reach || "workspace") as Reach;
  const reach: Reach =
    shellGranted(projects.data, workspace) && configured === "workspace" ? "workspace_shell" : configured;
  const approval = (cfg.data?.autonomy.approval || "suspicious") as Approval;
  const profile: Profile = "balanced";
  const runBusy = run.running && (run.workspace === null || run.workspace === workspace);

  return (
    // `overflow-x-hidden`: seen live, the conversation header's row of buttons ran past a narrow
    // window and the whole page scrolled sideways.
    <main aria-label={project} className="flex h-screen min-h-0 flex-col overflow-x-hidden bg-background text-foreground">
      <Conversation
        resumeSession={sessionId}
        workspace={workspace}
        openFile={null}
        onHandOff={(text) =>
          run.start({ task: text, verify: null, workspace: workspace || null, max_attempts: 3, profile })
        }
        runLive={run.running}
        onBatch={(tasks, conversation) =>
          startBatch({ tasks, workspace, posture: { reach, approval }, profile, sessionId: conversation ?? sessionId })
        }
        onEdited={() => {}}
        busyElsewhere={runBusy}
        posture={{ reach, approval }}
        provider={provider}
        model={model}
        style={style}
        onStyleRestored={(restored) => setStyle((current) => (current === "default" ? restored : current))}
        onModelRestored={(restored) => setModel((current) => (current === "" ? restored : current))}
        modelPinned={modelPinned}
        profile={profile}
        controls={
          <div className="flex flex-col gap-1.5">
            <ComposerSettings
              summary={[
                provider === "" ? t("code.provider.native") : provider,
                ...(provider === "" ? [model ? (model.split("/").pop() ?? model) : t("model.pick.default")] : []),
                ...(provider === "" && style !== "default" ? [styleLabel(t, style)] : []),
              ]}
            >
              <ProviderPicker value={provider} onChange={setProvider} disabled={runBusy} />
              {provider === "" ? <ModelPicker value={model} onChange={pickModel} disabled={runBusy} /> : null}
              {provider === "" ? <EffortPicker value={model} onChange={pickModel} disabled={runBusy} /> : null}
              {provider === "" ? <StylePicker value={style} onChange={setStyle} disabled={runBusy} /> : null}
            </ComposerSettings>
            <PostureNote workspace={workspace} reach={reach} approval={approval} provider={provider} />
          </div>
        }
      />
      {/* The batch board minimises and closes here as in the main window, but does not move: this window
          has no docks to move it to. The drag context is only what its frame is built on. */}
      <LayoutDnd>
        <AgentBatchSlot movable={false} />
      </LayoutDnd>
    </main>
  );
}
