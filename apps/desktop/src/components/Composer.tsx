import { useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Square } from "lucide-react";
import { Button } from "@/components/ui/button";

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

export function Composer({ busy, onSend, onStop }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const text = value.trim();
    if (!text || busy) return;
    onSend(text);
    setValue("");
    if (ref.current) ref.current.style.height = "auto";
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends; Shift+Enter makes a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="border-t border-border bg-background p-3">
      <div className="mx-auto flex max-w-3xl items-end gap-2 border border-input bg-card px-3 py-2">
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder="Message Chimera…"
          className="max-h-40 flex-1 resize-none bg-transparent py-1 text-sm outline-none placeholder:text-muted-foreground"
          onChange={(e) => {
            setValue(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={onKeyDown}
        />
        {busy ? (
          <Button size="icon" variant="outline" title="Stop" onClick={onStop}>
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button size="icon" title="Send" onClick={submit} disabled={!value.trim()}>
            <ArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
      <p className="mx-auto mt-1.5 max-w-3xl text-center text-[11px] text-muted-foreground">
        Enter to send · Shift+Enter for a new line
      </p>
    </div>
  );
}
