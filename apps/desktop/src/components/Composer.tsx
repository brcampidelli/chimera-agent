import { useRef, useState, type KeyboardEvent } from "react";
import { ArrowUp, Square, Network } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

interface Props {
  busy: boolean;
  onSend: (text: string, fuse: boolean) => void;
  onStop: () => void;
}

export function Composer({ busy, onSend, onStop }: Props) {
  const t = useT();
  const [value, setValue] = useState("");
  const [fuse, setFuse] = useState(false);
  const ref = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const text = value.trim();
    if (!text || busy) return;
    onSend(text, fuse);
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
    <div className="border-t border-white/5 p-3">
      <div className="field mx-auto flex max-w-3xl items-end gap-2 rounded-2xl px-3 py-2">
        <button
          type="button"
          aria-pressed={fuse}
          title={t("composer.fuseHint")}
          onClick={() => setFuse((f) => !f)}
          className={cn(
            "mb-0.5 flex h-8 shrink-0 items-center gap-1.5 rounded-chip px-2.5 text-xs font-medium transition-all",
            fuse
              ? "bg-accent-grad text-accent-foreground shadow-[0_0_12px_-2px_hsl(var(--accent)/0.75)]"
              : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
          )}
        >
          <Network className="h-3.5 w-3.5" />
          {t("composer.fuse")}
        </button>
        <textarea
          ref={ref}
          rows={1}
          value={value}
          placeholder={t("composer.placeholder")}
          className="max-h-40 flex-1 resize-none bg-transparent py-1 text-sm outline-none placeholder:text-muted-foreground"
          onChange={(e) => {
            setValue(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={onKeyDown}
        />
        {busy ? (
          <Button size="icon" variant="outline" title={t("composer.stop")} onClick={onStop}>
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button size="icon" title={t("composer.send")} onClick={submit} disabled={!value.trim()}>
            <ArrowUp className="h-4 w-4" />
          </Button>
        )}
      </div>
      <p className="mx-auto mt-1.5 max-w-3xl text-center text-[11px] text-muted-foreground">
        {fuse ? t("composer.fuseOn") : t("composer.hint")}
      </p>
    </div>
  );
}
