import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { IconRail, type View } from "@/components/IconRail";
import { Sessions } from "@/components/Sessions";
import { Chat } from "@/components/Chat";
import { Composer } from "@/components/Composer";
import { Settings } from "@/components/Settings";
import { Memory } from "@/components/Memory";
import { Skills } from "@/components/Skills";
import { Cron } from "@/components/Cron";
import { Tasks } from "@/components/Tasks";
import { Fusion } from "@/components/Fusion";
import { Usage } from "@/components/Usage";
import { Runs } from "@/components/Runs";
import { Code } from "@/components/Code";
import { Agents } from "@/components/Agents";
import { Governance } from "@/components/Governance";
import { Maturity } from "@/components/Maturity";
import { Tools } from "@/components/Tools";
import { Mcp } from "@/components/Mcp";
import { Onboarding } from "@/components/Onboarding";
import { VersionBadge } from "@/components/VersionBadge";
import { Activity, type Status } from "@/components/Activity";
import { Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { deleteSession, getDoctor, getSession, listSessions, streamChat } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Message, ToolEvent, TurnReport } from "@/lib/types";
import { applyTheme, readTheme, resolveTheme, type Theme } from "@/lib/theme";
import { useIgnition } from "@/lib/useIgnition";
import { cn } from "@/lib/utils";

/**
 * The theme preference, persisted.
 *
 * The inline script in index.html has already painted the right theme by the time this runs; this
 * hook exists so the toggle can change it. `dark` is the *resolved* theme (what is on screen), so
 * the icon always matches reality even while the preference is "system".
 */
function useTheme() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [dark, setDark] = useState(() => resolveTheme(readTheme()) === "dark");

  useEffect(() => {
    setDark(applyTheme(theme) === "dark");
  }, [theme]);

  useEffect(() => {
    // Follow the OS while the preference is "system" — someone flipping their system to night mode
    // mid-session should see the app follow, not wait for a restart.
    if (theme !== "system" || typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-color-scheme: light)");
    const onChange = () => setDark(applyTheme("system") === "dark");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  // The rail button is a two-state control over a three-state preference: tapping it commits to an
  // explicit choice, which is what someone reaching for the toggle means. "System" is set in
  // Settings › Appearance.
  return { dark, theme, setTheme, toggle: () => setTheme(dark ? "light" : "dark") };
}

export default function App() {
  const qc = useQueryClient();
  const t = useT();
  const { dark, toggle } = useTheme();
  // The app's one piece of choreography. Runs on cold start only; see useIgnition.
  const ignite = useIgnition();
  const { data: sessions = [] } = useQuery({ queryKey: ["sessions"], queryFn: listSessions });
  // First-run gate: no provider key => show the Onboarding wizard instead of the app (a keyed user
  // never sees it). Session-local "skip" lets a GUI-first user jump to Settings without a key yet.
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor });
  const [skipOnboarding, setSkipOnboarding] = useState(false);

  const [view, setView] = useState<View>("chat");
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [live, setLive] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [tools, setTools] = useState<ToolEvent[]>([]);
  const [report, setReport] = useState<TurnReport | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const openSession = useCallback(async (id: string) => {
    setView("chat");
    setCurrentId(id);
    setLive("");
    setTools([]);
    setReport(null);
    setStatus("idle");
    const data = await getSession(id);
    const msgs: Message[] = [];
    for (const t of data.turns) {
      msgs.push({ role: "user", content: t.user });
      msgs.push({ role: "assistant", content: t.assistant });
    }
    setMessages(msgs);
  }, []);

  const newChat = useCallback(() => {
    setView("chat");
    setCurrentId(null);
    setMessages([]);
    setLive("");
    setTools([]);
    setReport(null);
    setStatus("idle");
  }, []);

  const removeSession = useCallback(
    async (id: string) => {
      await deleteSession(id);
      if (id === currentId) newChat();
      qc.invalidateQueries({ queryKey: ["sessions"] });
    },
    [currentId, newChat, qc],
  );

  const send = useCallback(
    async (text: string, fuse = false) => {
      setMessages((m) => [...m, { role: "user", content: text }]);
      setBusy(true);
      setStatus("thinking");
      setLive("");
      setTools([]);
      setReport(null);
      const controller = new AbortController();
      abortRef.current = controller;

      await streamChat(
        text,
        currentId,
        {
          onSession: (id) => setCurrentId(id),
          onToken: (t) => {
            setStatus("streaming");
            setLive((prev) => prev + t);
          },
          onTool: (t) => setTools((prev) => [...prev, t]),
          onDone: (r) => {
            setMessages((m) => [...m, { role: "assistant", content: r.answer }]);
            setReport(r);
            setStatus("done");
            setLive("");
            qc.invalidateQueries({ queryKey: ["sessions"] });
          },
          onError: (msg) => {
            setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${msg}` }]);
            setStatus("idle");
            setLive("");
          },
        },
        controller.signal,
        fuse,
      );
      setBusy(false);
      abortRef.current = null;
    },
    [currentId, qc],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    setBusy(false);
    setStatus("idle");
    setLive("");
  }, []);

  // Gate the first render on the doctor's key check (all hooks above always run, so this stays a
  // safe early return). This is also the app's readiness gate: `doctor` is the first backend call,
  // so while the sidecar is still booting it retries (fast, per the QueryClient backoff) behind one
  // "starting…" screen — every inner screen then mounts against a warm backend.
  if (doctor.isLoading) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
        <Spinner />
        <span className="text-sm">{t("app.starting")}</span>
      </div>
    );
  }
  if (doctor.isError) {
    return (
      <div className="flex h-full items-center justify-center">
        <ErrorState error={doctor.error} onRetry={() => doctor.refetch()} />
      </div>
    );
  }
  if (doctor.data && !doctor.data.has_any_key && !skipOnboarding) {
    return (
      <Onboarding
        onSkip={() => {
          setSkipOnboarding(true);
          setView("settings");
        }}
      />
    );
  }

  return (
    <div className={cn("relative flex h-full", ignite && "ignite")}>
      {/* The ambient wash, lifted off `body` onto its own element so it can be animated. Purely
          decorative and behind everything, hence aria-hidden and pointer-events-none. */}
      <div
        aria-hidden
        {...(ignite && { "data-ignite": "wash" })}
        className="ambient-wash pointer-events-none fixed inset-0 -z-10"
      />
      <IconRail
        view={view}
        onSelect={setView}
        dark={dark}
        onToggleTheme={toggle}
        ignite={ignite}
      />
      {view === "chat" && (
        <div {...(ignite && { "data-ignite": "context" })} className="flex">
          <Sessions
            sessions={sessions}
            currentId={currentId}
            onSelect={openSession}
            onNew={newChat}
            onDelete={removeSession}
          />
        </div>
      )}
      <main
        {...(ignite && { "data-ignite": "main" })}
        className="flex min-w-0 flex-1 flex-col"
      >
        {view === "chat" && (
          <>
            <header className="relative flex items-center border-b border-hairline px-5 py-3">
              <span className="text-sm font-medium text-muted-foreground">
                {currentId
                  ? (sessions.find((s) => s.id === currentId)?.title ?? t("chat.header.chat"))
                  : t("chat.header.new")}
              </span>
              {/* The landing beat: one accent hairline drawing itself left to right. Everything
                  before it converges inward; this is the single gesture that says "arrived". */}
              {ignite && (
                <span
                  aria-hidden
                  data-ignite="sweep"
                  className="absolute inset-x-0 bottom-0 h-px bg-accent-grad"
                />
              )}
            </header>
            <Chat messages={messages} live={live} busy={busy} />
            <Composer busy={busy} onSend={send} onStop={stop} />
          </>
        )}
        {/* `key` is what makes the transition work at all: re-keying on the view forces React to
            replace the subtree, so the incoming screen mounts fresh and its enter animation runs.
            Only the entrance is animated — the outgoing screen leaves immediately, because holding
            it on screen to fade it out is what makes navigation feel slower than it is. */}
        {view !== "chat" && (
          <div key={view} className="view-enter flex min-h-0 flex-1 flex-col">
            {view === "memory" && <Memory />}
            {view === "skills" && <Skills />}
            {view === "cron" && <Cron />}
            {view === "tasks" && <Tasks />}
            {view === "fusion" && <Fusion report={report} />}
            {view === "usage" && <Usage />}
            {view === "runs" && <Runs />}
            {view === "code" && <Code />}
            {view === "agents" && <Agents />}
            {view === "tools" && <Tools />}
            {view === "mcp" && <Mcp />}
            {view === "governance" && <Governance />}
            {view === "maturity" && <Maturity />}
            {view === "settings" && <Settings />}
          </div>
        )}
      </main>
      {view === "chat" && (
        <div {...(ignite && { "data-ignite": "inspector" })} className="flex">
          <Activity status={status} tools={tools} report={report} />
        </div>
      )}
      {/* Low-key version indicator in the bottom corner (like the Hermes app's). Shows the running
          version, and an accent "update available" pill only when GitHub confirms a newer release. */}
      <div className="fixed bottom-2 right-3 z-40">
        <VersionBadge />
      </div>
    </div>
  );
}
