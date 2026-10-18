import { Suspense, lazy, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { IconRail, type View } from "@/components/IconRail";
import { useRoute } from "@/lib/router";
import { legacyRedirect } from "@/lib/legacyRoutes";
import { Settings, settingsTab } from "@/components/Settings";
import { Knowledge, knowledgeTab } from "@/components/Knowledge";
import { Automation, automationTab } from "@/components/Automation";
import { Code } from "@/components/Code";
import { Work } from "@/components/Work";
import { Maturity } from "@/components/Maturity";
import { Onboarding } from "@/components/Onboarding";
import { Activity } from "@/components/Activity";
import { BackendDown } from "@/components/BackendDown";
import { AppShell } from "@/components/shell/AppShell";
import { CommandPalette, type Command } from "@/components/shell/CommandPalette";
import { shortcutLabel, useHotkeys } from "@/lib/hotkeys";
import { AgentBatchProvider } from "@/lib/agent-batch";
import { AgentProvider } from "@/lib/agent-context";
import { ConversationSideProvider } from "@/lib/conversation-side";
import { FloatHost } from "@/lib/float/host";
import { RunSessionProvider } from "@/lib/run-session";
import { Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { getDoctor, listArchivedCodeSessions, listCodeSessions } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLayout } from "@/lib/layout/context";
import { PANELS, type PanelId, type TranscriptWidth } from "@/lib/layout/model";
import { loadMine, saveMine } from "@/lib/layout/store";
import { useToast } from "@/components/ui/toast";
import { AppearanceProvider, useAppearance } from "@/lib/appearance";
import { readWorkspace, writeLastSession, writeWorkspace } from "@/lib/workspace";
import { useIgnition } from "@/lib/useIgnition";
import { ShortcutsDialog } from "@/components/shell/ShortcutsDialog";
import { requestAppCommand, requestOpenConversation } from "@/lib/appCommands";
import { conversationCommands as storedConversationCommands, type PaletteSession } from "@/lib/paletteConversations";
import { stepUiZoom } from "@/lib/zoomSteps";

/**
 * The editor arrives on demand.
 *
 * CodeMirror and its six grammars weigh 609 kB (215 kB gzipped) — measured by building both ways —
 * on a screen most sessions never open. Eager, they grew the bundle by 30%; deferred, the main
 * bundle moves by under 10 kB. Everything else here is loaded up front because it is small and
 * because a split you cannot justify is just a loading state you invented; this one pays for
 * itself, and it is the pattern the language server and the completion model will need next.
 *
 * Splitting is possible at all because the screen has an ADDRESS. Deciding what to load by looking
 * at the route is the first thing the router bought that a `useState` could not.
 */
const Edit = lazy(() => import("@/components/Edit").then((m) => ({ default: m.Edit })));
const EditSidebar = lazy(() =>
  import("@/components/editor/EditSidebar").then((m) => ({ default: m.EditSidebar })),
);

/** A chunk that is still arriving. Centred and quiet — this is a fraction of a second on local
 *  disk, and a heavy skeleton would be more noticeable than the wait it covers. */
function Loading({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <Spinner />
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

/**
 * How often the app asks the backend whether it is still there.
 *
 * Exported because the test drives this exact interval: a poll the test hardcodes could pass while
 * the app polls once an hour.
 */
export const HEARTBEAT_MS = 5_000;

/** The palette's width commands. Keys written out so the i18n reachability test can see them. */
const transcriptWidths: { value: TranscriptWidth; key: string }[] = [
  { value: "narrow", key: "layout.transcript.narrow" },
  { value: "medium", key: "layout.transcript.medium" },
  { value: "wide", key: "layout.transcript.wide" },
];

/**
 * When the backend comes back, tell every other query about it.
 *
 * React Query does not retry a query that already failed because a DIFFERENT one succeeded, so
 * without this the app reconnects and still looks broken: the heartbeat goes green, the banner
 * disappears, and every panel underneath keeps its error state until the user clicks its own "Try
 * again" by hand. Invalidating once on the down→up edge (never on every render, and never while
 * still down) is what turns a reconnection into a recovery.
 */
function useRecoverFromOutage(down: boolean): void {
  const queryClient = useQueryClient();
  const wasDown = useRef(false);
  useEffect(() => {
    if (down) {
      wasDown.current = true;
      return;
    }
    if (wasDown.current) {
      wasDown.current = false;
      void queryClient.invalidateQueries();
    }
  }, [down, queryClient]);
}

/**
 * The app, with room above it for a bar that is usually not there.
 *
 * `#root` is 100% tall and everything under it asks for `h-full`, so a banner added as a plain
 * sibling would push the shell's own footer past the bottom of the window. One flex column, with
 * the shell in a `min-h-0 flex-1` cell, is what lets the bar take its ~40 pixels from the app
 * instead of from the viewport.
 */
function Framed({ banner, children }: { banner: ReactNode; children: ReactNode }) {
  return (
    <div className="flex h-full flex-col">
      {banner}
      <div className="min-h-0 flex-1">{children}</div>
    </div>
  );
}

/**
 * The app, with its appearance held above it.
 *
 * The provider sits here rather than in `main.tsx` so every way of mounting the app — the real one
 * and each test that mounts it whole — gets the same theme the rail button and Settings agree on.
 */
export default function App() {
  return (
    <AppearanceProvider>
      <Workspace />
    </AppearanceProvider>
  );
}

function Workspace() {
  const t = useT();
  const { dark, toggleTheme: toggle } = useAppearance();
  const [helpOpen, setHelpOpen] = useState(false);
  // The app's one piece of choreography. Runs on cold start only; see useIgnition.
  const ignite = useIgnition();
  // First-run gate: no way to answer (no provider key AND no local model) => show the Onboarding
  // wizard instead of the app (a keyed user, or one whose default model runs on this machine,
  // never sees it). Session-local "skip" lets a GUI-first user jump to Settings without a key yet.
  //
  // It is also the app's heartbeat, and that is the second job this one call now does. Without the
  // interval it ran once at mount and never again, so the error branch below could only ever
  // describe a backend that never came up — while a backend that DIED mid-session left every panel
  // showing its own "Try again" (each of which would fail forever) and nothing on screen saying
  // why. One query that every screen already waits for is the honest place to ask the question.
  //
  // The retry policy in `main.tsx` doubles as the de-bounce: six quick retries mean the ordinary
  // case — the shell noticing a dead sidecar and replacing it within a couple of seconds — never
  // reaches `isError` at all, so nothing flashes on screen for an outage the user never had.
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor, refetchInterval: HEARTBEAT_MS });
  const backendDown = doctor.isError;
  useRecoverFromOutage(backendDown);
  const [skipOnboarding, setSkipOnboarding] = useState(false);
  // The screen lives in the URL now. It was a `useState`, which is enough until something outside
  // this switch needs to point at a place — "open this file at line 42" from a diagnostic or a
  // search hit — and until open editor tabs need to survive a reload and answer the back button.
  const { route, view, navigate, setParams, replace } = useRoute();
  // An address that has moved — `#/knowledge?tab=skills` above all, now Settings › Skills — is
  // rewritten in place, before paint, so the old screen never flashes and the back button does not
  // return to an entry that would only be rewritten again.
  useLayoutEffect(() => {
    const moved = legacyRedirect(route);
    if (moved) replace(moved.view, moved.params);
  }, [route, replace]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // The editor's folder. It lives here rather than inside the screen because the sidebar and the
  // editor are two different AppShell slots, so their common state has to sit above both. Same
  // stored value the chat screen uses — one app, one current project.
  const [editWorkspace, setEditWorkspace] = useState(readWorkspace);
  // Re-read on the way in, because "one app, one current project" was a promise this file made
  // and did not keep. The initialiser runs once, when the app starts; the chat screen writes the
  // stored value whenever you switch projects and re-reads it on every navigation, since its
  // subtree is re-keyed. This one is not, so choosing a project in Code and opening the editor
  // showed the folder you had open when the app launched — with nothing on screen saying the two
  // disagreed, and no way back short of restarting.
  useEffect(() => {
    if (view === "edit") setEditWorkspace(readWorkspace());
  }, [view]);
  const editPath = route.params.get("file");
  // The line a search hit asked for, in the URL beside the file so a reload lands there too.
  const editLineParam = Number(route.params.get("line") ?? "");
  const editLine = Number.isInteger(editLineParam) && editLineParam > 0 ? editLineParam : null;
  const openInEditor = (file: string | null, line?: number) => {
    const params: Record<string, string> | undefined = file ? (line ? { file, line: String(line) } : { file }) : undefined;
    return view === "edit" && params ? setParams(params) : navigate("edit", params);
  };

  // Every destination, plus every session by title. This is the long tail the rail no longer
  // carries — and the reason collapsing the rail cost nothing in reach.
  // And the layout's own commands. "Restore the default layout" is always here, even with nothing
  // hidden: the status bar shows nothing at zero, so this is where the way back lives meanwhile.
  const { dispatch: layoutDispatch, hidden, layout } = useLayout();
  const anyHidden = hidden.length > 0;
  const toast = useToast();
  const focusOn = layout.beforeFocus !== null;
  const shown = {
    left: layout.regions.left.visible,
    right: layout.regions.right.visible,
    rail: layout.regions.rail.visible,
  };
  // Said out loud, because a 10% step is easy to make by accident and hard to see.
  const changeZoom = useCallback(
    (direction: "in" | "out" | "reset") => {
      const next = stepUiZoom(direction);
      toast(t("zoom.now", { n: String(next) }), "info");
    },
    [t, toast],
  );
  // The conversations, asked for only while the palette is open (same keys as the sidebar, so the
  // same cache). Lazy `queryFn`s: the binding is read when a fetch runs, not on every render.
  const paletteSessions = useQuery({
    queryKey: ["code-sessions"],
    queryFn: () => listCodeSessions(),
    enabled: paletteOpen,
  });
  const paletteArchived = useQuery({
    queryKey: ["code-sessions", "archived"],
    queryFn: () => listArchivedCodeSessions(),
    enabled: paletteOpen,
  });
  const openConversation = useCallback(
    (session: PaletteSession) => {
      // Stored first, so a Code screen that mounts because of this navigation opens on it; one already
      // mounted is asked directly and lands where a click on its sidebar row lands.
      writeWorkspace(session.workspace);
      writeLastSession(session.workspace, session.id);
      if (view === "code") requestOpenConversation({ id: session.id, workspace: session.workspace });
      else navigate("code");
    },
    [view, navigate],
  );
  const sessionCommands: Command[] = useMemo(
    () => storedConversationCommands(paletteSessions.data ?? [], paletteArchived.data ?? [], t, openConversation),
    [paletteSessions.data, paletteArchived.data, t, openConversation],
  );
  const commands: Command[] = useMemo(() => {
    const views: View[] = ["code", "edit", "work", "knowledge", "automation", "settings"];
    const go = views.map((v) => ({
      id: `go-${v}`,
      label: t(`nav.${v}`),
      group: t("palette.group.go"),
      shortcut: shortcutLabel(v === "settings" ? "settings" : `navigate${views.indexOf(v) + 1}`),
      run: () => navigate(v),
    }));
    // The three Personalization tabs by name. Skills was reached through Knowledge and MCP through
    // Connections; the palette names where they are now, never the old address (study 04, §1.3).
    const personalize: Command[] = (["skills", "connectors", "plugins"] as const).map((tab) => ({
      id: `go-settings-${tab}`,
      label: t(`settings.tab.${tab}`),
      group: t("palette.group.go"),
      run: () => navigate("settings", { tab }),
    }));
    // One command per side region, worded for what it will do now: "Hide…" while it is shown.
    const toggles: Command[] = (["left", "right", "rail"] as const).map((side) => ({
      id: `layout-toggle-${side}`,
      label: t(shown[side] ? `layout.hide.${side}` : `layout.show.${side}`),
      group: t("palette.group.layout"),
      run: () => void layoutDispatch({ type: "set-region", region: side, visible: !shown[side] }),
    }));
    // Layouts one command away (phase 5), and the person's own, saved and applied from here.
    const mine = loadMine();
    const presets: Command[] = [
      { id: "layout-focus", label: t(focusOn ? "layout.focus.exit" : "layout.focus.enter"), group: t("palette.group.layout"),
        run: () => void layoutDispatch({ type: "toggle-focus" }) },
      { id: "layout-review", label: t("layout.preset.review"), group: t("palette.group.layout"),
        run: () => void layoutDispatch({ type: "preset", name: "review" }) },
      { id: "layout-monitor", label: t("layout.preset.monitor"), group: t("palette.group.layout"),
        run: () => void layoutDispatch({ type: "preset", name: "monitor" }) },
      // Every card kind at once, one step to undo. Approval, spend warnings and a failed turn's error
      // stay open in both (`cardPresetActions` checks each kind); a card's corner still changes one.
      { id: "layout-cards-compact", label: t("layout.cards.compact"), group: t("palette.group.layout"),
        run: () => void layoutDispatch({ type: "card-preset", name: "compact" }) },
      { id: "layout-cards-detailed", label: t("layout.cards.detailed"), group: t("palette.group.layout"),
        run: () => void layoutDispatch({ type: "card-preset", name: "detailed" }) },
      // The conversation's width, offering the two it is not now.
      ...transcriptWidths
        .filter((width) => width.value !== layout.transcriptWidth)
        .map((width) => ({
          id: `layout-transcript-${width.value}`,
          label: t(width.key),
          group: t("palette.group.layout"),
          run: () => void layoutDispatch({ type: "transcript-width", width: width.value }),
        })),
      { id: "layout-save-mine", label: t("layout.mine.save"), group: t("palette.group.layout"),
        run: () => {
          if (saveMine(layout)) toast(t("layout.mine.saved"), "ok");
        } },
      ...(mine
        ? [{ id: "layout-apply-mine", label: t("layout.mine.apply"), group: t("palette.group.layout"),
             run: () => void layoutDispatch({ type: "apply", layout: mine }) }]
        : []),
    ];
    const layoutCommands: Command[] = [
      ...toggles,
      ...presets,
      ...(anyHidden
        ? [{ id: "layout-show-all", label: t("layout.hidden.showAll"), group: t("palette.group.layout"),
             run: () => void layoutDispatch({ type: "show-all" }) }]
        : []),
      { id: "layout-reset", label: t("layout.reset"), group: t("palette.group.layout"),
        run: () => void layoutDispatch({ type: "reset" }) },
    ];
    // Every command here that has a key says so, from the same table the listener matches on.
    const globalCommands: Command[] = [
      { id: "open-help", label: t("hotkeys.help"), group: t("palette.group.go"), shortcut: shortcutLabel("help"), run: () => setHelpOpen(true) },
      { id: "zoom-in", label: t("hotkeys.zoomIn"), group: t("palette.group.layout"), shortcut: shortcutLabel("zoomIn"), run: () => changeZoom("in") },
      { id: "zoom-out", label: t("hotkeys.zoomOut"), group: t("palette.group.layout"), shortcut: shortcutLabel("zoomOut"), run: () => changeZoom("out") },
      { id: "zoom-reset", label: t("hotkeys.zoomReset"), group: t("palette.group.layout"), shortcut: shortcutLabel("zoomReset"), run: () => changeZoom("reset") },
    ];
    // The conversation's own commands, only while it is on screen: they act on state that lives in
    // that screen, and are asked for by name (src/lib/appCommands.ts) rather than copied here.
    const conversation = t("palette.group.conversation");
    const conversationCommands: Command[] =
      view === "code"
        ? [
            { id: "reopen-conversation", label: t("hotkeys.reopenConversation"), group: conversation,
              shortcut: shortcutLabel("reopenConversation"), run: () => requestAppCommand("reopenConversation") },
            { id: "previous-conversation", label: t("hotkeys.previousConversation"), group: conversation,
              shortcut: shortcutLabel("previousConversation"), run: () => requestAppCommand("previousConversation") },
            { id: "next-conversation", label: t("hotkeys.nextConversation"), group: conversation,
              shortcut: shortcutLabel("nextConversation"), run: () => requestAppCommand("nextConversation") },
            // Keeps the focus it moves into the find field: the palette must not hand it back on close.
            { id: "find-in-conversation", label: t("hotkeys.find"), group: conversation,
              shortcut: shortcutLabel("find"), takesFocus: true, run: () => requestAppCommand("find") },
          ]
        : [];
    return [...go, ...personalize, ...conversationCommands, ...sessionCommands, ...globalCommands, ...layoutCommands];
  }, [t, navigate, layoutDispatch, anyHidden, shown.left, shown.right, shown.rail, focusOn, layout, toast, changeZoom, view, sessionCommands]);

  useHotkeys({
    onPalette: () => setPaletteOpen((o) => !o),
    onSettings: () => navigate("settings"),
    // The conversation owns its own "new" now — it lives beside the list of past ones, which is
    // where someone looks for it. A global shortcut that jumps you to a screen AND clears it is two
    // actions wearing one key.
    onNewChat: () => navigate("code"),
    onHelp: () => setHelpOpen(true),
    onZoom: changeZoom,
    onFocusMode: () => void layoutDispatch({ type: "toggle-focus" }),
    // The panel that holds focus, or the one already maximised: ⌘⇧M goes in and out of the same place.
    onMaximize: () => {
      if (layout.maximized) return void layoutDispatch({ type: "maximize", panel: null });
      const id = (document.activeElement as HTMLElement | null)?.closest("[data-panel]")?.getAttribute("data-panel");
      if (id && id in PANELS && PANELS[id as PanelId].maximizable) {
        layoutDispatch({ type: "maximize", panel: id as PanelId });
      }
    },
    // The approval waiting in the conversation, minimised or not: brought into view with focus on its
    // first control, which is the one that opens it again when it was minimised.
    onApproval: () => {
      const card = document.querySelector<HTMLElement>('[data-card="approval"]');
      card?.scrollIntoView({ block: "center" });
      card?.querySelector<HTMLElement>("button:not([aria-disabled='true'])")?.focus();
    },
    onToggleRegion: (side) =>
      void layoutDispatch({ type: "set-region", region: side, visible: !shown[side] }),
    onNavigate: (i) => {
      // The same order as the rail, which is the only reason a number key is guessable at all.
      const order: View[] = ["code", "edit", "work", "knowledge", "automation"];
      const target = order[i - 1];
      if (target) navigate(target);
    },
  });

  // The bar goes above whatever is on screen, including the onboarding wizard — which needs the
  // backend exactly as much as the rest of the app does.
  const outage = backendDown ? <BackendDown onRetry={() => void doctor.refetch()} /> : null;

  if (doctor.isLoading) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <Spinner />
        <span className="text-sm">{t("app.starting")}</span>
      </div>
    );
  }
  // A backend that has NEVER answered is a different situation from one that stopped: there is no
  // app underneath to put a bar on top of, so this stays the whole-screen state it always was.
  if (backendDown && !doctor.data) {
    return (
      <div className="flex h-full items-center justify-center">
        <ErrorState error={doctor.error} onRetry={() => doctor.refetch()} />
      </div>
    );
  }
  // `can_answer`, not `has_any_key`: a default model that runs on this machine needs no key, and
  // gating on keys alone showed a keyless Ollama user a wizard demanding one.
  if (doctor.data && !doctor.data.can_answer && !skipOnboarding) {
    return (
      <Framed banner={outage}>
        <Onboarding
          onSkip={() => {
            setSkipOnboarding(true);
            navigate("settings");
          }}
        />
      </Framed>
    );
  }

  return (
    // Stateful now: whichever screen is working publishes into it, so the footer keeps showing the
    // agent from anywhere. It used to be fed by the chat screen alone, which meant the merged
    // conversation would have left it mute.
    <AgentProvider>
      {/* What the Code screen's side cards describe (C5): published by the screen and its
          conversation, read by the cards in the shell's docks, which sit outside the screen. */}
      <ConversationSideProvider>
      {/* Above the view switch on purpose: a run outlives the screen that started it, so its state
          cannot live inside that screen. This is what keeps the progress and the Stop alive when
          you navigate away mid-run. */}
      <RunSessionProvider>
      {/* The parallel batch, above the view switch for the same reason as the run: it outlives its board,
          which can be closed, minimised or moved to a dock, and outlives the Code screen too. */}
      <AgentBatchProvider>
      {/* Panels in windows of their own (phase 7). Inside the agent's provider: it sends that state on. */}
      <FloatHost>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} commands={commands} />
      <ShortcutsDialog open={helpOpen} onOpenChange={setHelpOpen} />
      <Framed banner={outage}>
      <AppShell
        ignite={ignite}
        viewKey={view}
        viewLabel={t(`nav.${view}`)}
        // Usage lives in Settings now; the cost chip still takes you straight there — to the Usage
        // TAB, which needs the tab in the route: a bare `navigate("settings")` opened General.
        onOpenUsage={() => navigate("settings", { tab: "usage" })}
        rail={
          <IconRail
            view={view}
            onSelect={navigate}
            dark={dark}
            onToggleTheme={toggle}
            ignite={ignite}
          />
        }
        // Right = what the agent is doing. The conversation keeps its own list of past sessions
        // inside itself (grouped by project, which the flat chat list could not do), so the left
        // slot stays empty rather than holding a second list of the same thing.
        inspector={view === "code" ? <Activity /> : undefined}
        // Before the first turn the activity column was a stack of empty panels taking a quarter of
        // the screen; it now appears when there is a turn to watch.
        inspectorFollowsAgent={view === "code"}
        // Left = what exists. On the editor screen that is the folder you are working in — the one
        // place in the app where finding the file IS the task rather than a detour from it.
        context={
          view === "edit" ? (
            <Loading>
              <EditSidebar
                workspace={editWorkspace}
                onWorkspace={(w) => {
                  setEditWorkspace(w);
                  writeWorkspace(w);
                }}
                activePath={editPath}
                onOpen={openInEditor}
                onOpenAt={openInEditor}
              />
            </Loading>
          ) : undefined
        }
      >
        {/* Each tab has an address, like Settings': the first tab is the bare screen, so the common
            URL stays readable, and switching tabs replaces rather than pushes. */}
        {view === "knowledge" && (
          <Knowledge
            tab={knowledgeTab(route.params.get("tab"))}
            // Skills still has a tab here until a release has carried the redirect; choosing it goes
            // to where Skills lives now, as a step the back button can undo.
            onTabChange={(next) =>
              next === "skills"
                ? navigate("settings", { tab: "skills" })
                : setParams(next === "memory" ? {} : { tab: next })
            }
          />
        )}
        {view === "automation" && (
          <Automation
            tab={automationTab(route.params.get("tab"))}
            onTabChange={(next) => setParams(next === "schedule" ? {} : { tab: next })}
          />
        )}
        {view === "work" && <Work />}
        {view === "code" && <Code />}
        {view === "edit" && (
          <Loading>
            <Edit workspace={editWorkspace || null} path={editPath} line={editLine} onOpen={(file) => openInEditor(file)} />
          </Loading>
        )}
        {/* Dev-only, matching the rail: in a shipped build this screen has no data. */}
        {view === "maturity" && import.meta.env.DEV && <Maturity />}
        {view === "settings" && (
          <Settings
            tab={settingsTab(route.params.get("tab"))}
            // Replaces rather than pushes (see `setParams`): clicking through tabs should not fill
            // the back button. General is the bare `#/settings`, so the common URL stays readable.
            onTabChange={(next) => setParams(next === "general" ? {} : { tab: next })}
          />
        )}
      </AppShell>
      </Framed>
      </FloatHost>
      </AgentBatchProvider>
      </RunSessionProvider>
      </ConversationSideProvider>
    </AgentProvider>
  );
}
