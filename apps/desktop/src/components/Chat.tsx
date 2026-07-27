import { useRef } from "react";
import Markdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import { cn } from "@/lib/utils";
import { BrandMark } from "@/components/BrandMark";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { useStickToBottom } from "@/lib/useStickToBottom";
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
      <BrandMark className="mt-0.5 h-6 w-6 shrink-0" alt="Chimera" />
      <div className={cn("md min-w-0 flex-1 text-base leading-relaxed")}>
        {streaming ? (
          <span className="whitespace-pre-wrap">
            {content}
            {/* `caret` blinks on steps(1) — hard on, hard off, like a text cursor. `animate-pulse`
                is an eased sine, which reads as a heartbeat and subtly implies waiting. */}
            <span className="caret ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 bg-accent" />
          </span>
        ) : (
          <Markdown rehypePlugins={[rehypeHighlight]}>{content}</Markdown>
        )}
      </div>
    </div>
  );
}

function Empty() {
  const t = useT();
  return (
    <div className="flex h-full flex-col items-center justify-center py-24 text-center">
      <BrandMark className="mb-4 h-16 w-16" glow />
      <h1 className="text-lg font-semibold">Chimera</h1>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{t("chat.empty.body")}</p>
    </div>
  );
}

export function Chat({ messages, live, busy }: Props) {
  const t = useT();
  const scrollRef = useRef<HTMLDivElement>(null);
  // Follows the stream by writing scrollTop once per frame, and stops the moment the reader scrolls
  // up. The previous smooth-scroll-per-token both lagged behind the text and yanked the reader back.
  const { stuck, scrollToBottom } = useStickToBottom(scrollRef, [messages, live]);

  return (
    <div className="relative flex-1 overflow-hidden">
      <div ref={scrollRef} className="h-full overflow-y-auto">
        {/* role="log" tells a screen reader this region accumulates; aria-busy says the agent is
            still writing. The streaming text is deliberately NOT a live region — announcing it
            would re-read the whole growing answer on every token. The state announcements live in
            the status region instead. */}
        <div
          role="log"
          aria-busy={busy}
          className="mx-auto max-w-3xl space-y-6 px-4 py-6"
        >
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
        </div>
      </div>

      {/* Only while there is something to miss: reading back through a finished answer shouldn't
          nag you to return to the bottom. */}
      {!stuck && busy && (
        <button
          type="button"
          onClick={scrollToBottom}
          className={cn(
            "absolute bottom-4 left-1/2 -translate-x-1/2 rounded-chip border border-hairline",
            "bg-card px-3 py-1.5 text-xs text-muted-foreground shadow-elev",
            "transition duration-1 ease-out hover:text-foreground",
            focusRing,
          )}
        >
          {t("chat.jumpToLatest")}
        </button>
      )}
    </div>
  );
}
