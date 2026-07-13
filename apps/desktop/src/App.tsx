import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Moon, Sun } from "lucide-react";
import { Sessions } from "@/components/Sessions";
import { Chat } from "@/components/Chat";
import { Composer } from "@/components/Composer";
import { Activity, type Status } from "@/components/Activity";
import { Button } from "@/components/ui/button";
import { deleteSession, getSession, listSessions, streamChat } from "@/lib/api";
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
  const { dark, toggle } = useTheme();
  const { data: sessions = [] } = useQuery({ queryKey: ["sessions"], queryFn: listSessions });

  const [currentId, setCurrentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [live, setLive] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>("idle");
  const [tools, setTools] = useState<ToolEvent[]>([]);
  const [report, setReport] = useState<TurnReport | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const openSession = useCallback(async (id: string) => {
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
    async (text: string) => {
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

  return (
    <div className="flex h-full">
      <Sessions
        sessions={sessions}
        currentId={currentId}
        onSelect={openSession}
        onNew={newChat}
        onDelete={removeSession}
      />
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border px-4 py-2.5">
          <span className="text-sm font-medium text-muted-foreground">
            {currentId ? (sessions.find((s) => s.id === currentId)?.title ?? "Chat") : "New chat"}
          </span>
          <Button size="icon" variant="ghost" onClick={toggle} title="Toggle theme">
            {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </Button>
        </header>
        <Chat messages={messages} live={live} busy={busy} />
        <Composer busy={busy} onSend={send} onStop={stop} />
      </main>
      <Activity status={status} tools={tools} report={report} />
    </div>
  );
}
