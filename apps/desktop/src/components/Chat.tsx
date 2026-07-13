import { useEffect, useRef } from "react";
import Markdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import { cn } from "@/lib/utils";
import type { Message } from "@/lib/types";

interface Props {
  messages: Message[];
  live: string;
  busy: boolean;
}

function Bubble({
  role,
  content,
  streaming,
}: {
  role: "user" | "assistant";
  content: string;
  streaming?: boolean;
}) {
  if (role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-accent-grad px-4 py-2 text-accent-foreground shadow-btn">
          {content}
        </div>
      </div>
    );
  }
  return (
    <div className="flex gap-3">
      <span className="mt-0.5 select-none text-lg" aria-hidden>
        🔺
      </span>
      <div className={cn("md min-w-0 flex-1 text-[15px] leading-relaxed")}>
        {streaming ? (
          <span className="whitespace-pre-wrap">
            {content}
            <span className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse bg-accent" />
          </span>
        ) : (
          <Markdown rehypePlugins={[rehypeHighlight]}>{content}</Markdown>
        )}
      </div>
    </div>
  );
}

function Empty() {
  return (
    <div className="flex h-full flex-col items-center justify-center py-24 text-center">
      <div className="mb-3 text-4xl" aria-hidden>
        🔺
      </div>
      <h1 className="text-lg font-semibold">Chimera</h1>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">
        Ask anything. Tokens stream live; the panel on the right shows the tools, cost, and memory
        each turn actually used.
      </p>
    </div>
  );
}

export function Chat({ messages, live, busy }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, live]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
        {messages.length === 0 && !live ? (
          <Empty />
        ) : (
          <>
            {messages.map((m, i) => (
              <Bubble key={i} role={m.role} content={m.content} />
            ))}
            {busy && live && <Bubble role="assistant" content={live} streaming />}
          </>
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
