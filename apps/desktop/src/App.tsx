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
import { Activity, type Status } from "@/components/Activity";
import { Spinner } from "@/components/ui/panel";
import { deleteSession, getDoctor, getSession, listSessions, streamChat } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Message, ToolEvent, TurnReport } from "@/lib/types";

function useTheme() {
  const [dark, setDark] = useState(
    () =>
      document.documentElement.dataset.theme === "dark" ||
      (!document.documentElement.dataset.theme &&
        window.matchMedia("(prefers-color-scheme: dark)").matches),
  );
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

export default function App() {
  const qc = useQueryClient();
  const t = useT();
  const { dark, toggle } = useTheme();
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
  // safe early return). While loading, a spinner; no key + not skipped => the setup wizard.
  if (doctor.isLoading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner />
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
    <div className="flex h-full">
      <IconRail view={view} onSelect={setView} dark={dark} onToggleTheme={toggle} />
      {view === "chat" && (
        <Sessions
          sessions={sessions}
          currentId={currentId}
          onSelect={openSession}
          onNew={newChat}
          onDelete={removeSession}
        />
      )}
      <main className="flex min-w-0 flex-1 flex-col">
        {view === "chat" && (
          <>
            <header className="flex items-center border-b border-white/5 px-5 py-3">
              <span className="text-sm font-medium text-muted-foreground">
                {currentId
                  ? (sessions.find((s) => s.id === currentId)?.title ?? t("chat.header.chat"))
                  : t("chat.header.new")}
              </span>
            </header>
            <Chat messages={messages} live={live} busy={busy} />
            <Composer busy={busy} onSend={send} onStop={stop} />
          </>
        )}
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
      </main>
      {view === "chat" && <Activity status={status} tools={tools} report={report} />}
    </div>
  );
}
