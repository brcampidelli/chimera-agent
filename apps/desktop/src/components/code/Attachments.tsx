import { useRef, useState } from "react";
import {
  AlertTriangle,
  FileText,
  Image as ImageIcon,
  Loader2,
  Mic,
  Paperclip,
  Square,
  X,
} from "lucide-react";

import { useQuery } from "@tanstack/react-query";

import {
  getDictationSupport,
  getVisionSupport,
  transcribe,
  uploadAttachment,
  type Attachment,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** The two ways to put something into a message that is not typing.
 *
 * Attach and dictate live together because they solve the same problem from opposite ends: one gets
 * content INTO the conversation that was never text, and the other gets text in without a keyboard.
 *
 * Both are deliberately honest about failing. An upload that could not be read reports why, rather
 * than sitting in the tray looking like it worked; and a transcription that failed shows its reason
 * instead of pasting an error message into the composer as if it were what you said — which is the
 * failure mode that would actually get sent to a model.
 */

export function AttachmentTray({
  items,
  onRemove,
}: {
  items: Attachment[];
  onRemove: (id: string) => void;
}) {
  const t = useT();
  const hasImage = items.some((a) => a.kind === "image");
  // Asked only once an image is actually attached: it is a question about THIS message, and asking
  // it up front would put a caveat about vision on a screen where nobody had mentioned pictures.
  const vision = useQuery({
    queryKey: ["vision"],
    queryFn: getVisionSupport,
    enabled: hasImage,
    staleTime: 60_000,
  });

  if (items.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 pb-1.5">
      {items.map((a) => (
        <span
          key={a.id}
          className={cn(
            "flex items-center gap-1.5 rounded-chip border px-2 py-1 text-xs",
            a.note ? "border-warn/40 text-warn" : "border-border text-muted-foreground",
          )}
          title={a.note || undefined}
        >
          {a.kind === "image" ? (
            <ImageIcon className="h-3.5 w-3.5" />
          ) : (
            <FileText className="h-3.5 w-3.5" />
          )}
          <span className="max-w-[14rem] truncate">{a.name}</span>
          {/* A document that yielded no text is not the same as one nobody opened. Saying which is
              the difference between "the model read this" and "the model was told a filename". */}
          {a.kind === "document" && !a.note ? (
            <span className="text-muted-foreground">{t("code.attach.chars", { n: a.chars })}</span>
          ) : null}
          <button
            type="button"
            onClick={() => onRemove(a.id)}
            aria-label={t("code.attach.remove", { name: a.name })}
            className="text-muted-foreground hover:text-bad"
          >
            <X className="h-3 w-3" />
          </button>
        </span>
      ))}
      {/* An image sent to a model without vision is a provider error at best and silence at worst —
          and an answer about a picture nobody looked at reads exactly like one about a picture that
          was. Three states, because "we have never heard of this model" is not "this model is
          blind": saying the second would send someone off to disable something that works. */}
      {hasImage && vision.data && vision.data.support !== "yes" ? (
        <span
          className={cn(
            "flex items-center gap-1.5 text-xs",
            vision.data.support === "no" ? "text-bad" : "text-warn",
          )}
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          {t(
            vision.data.support === "no" ? "code.attach.modelBlind" : "code.attach.visionUnknown",
            { model: vision.data.model },
          )}
        </span>
      ) : null}
    </div>
  );
}

export function AttachButton({ onAdded }: { onAdded: (a: Attachment) => void }) {
  const t = useT();
  const input = useRef<HTMLInputElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState("");

  async function pick(files: FileList | null) {
    if (!files?.length) return;
    setBusy(true);
    setFailed("");
    for (const file of Array.from(files)) {
      try {
        onAdded(await uploadAttachment(file));
      } catch {
        // Named, because "one of the four you picked did not arrive" is unusable as a warning.
        setFailed(file.name);
      }
    }
    setBusy(false);
    if (input.current) input.current.value = ""; // so the same file can be picked twice
  }

  return (
    <>
      <input
        ref={input}
        type="file"
        multiple
        className="hidden"
        aria-label={t("code.attach.label")}
        onChange={(e) => void pick(e.target.files)}
      />
      <Button
        size="sm"
        variant="ghost"
        disabled={busy}
        title={t("code.attach.hint")}
        onClick={() => input.current?.click()}
      >
        {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
        {t("code.attach.label")}
      </Button>
      {failed ? <span className="text-xs text-bad">{t("code.attach.failed", { name: failed })}</span> : null}
    </>
  );
}

export function DictateButton({ onText }: { onText: (text: string) => void }) {
  const t = useT();
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const [state, setState] = useState<"idle" | "recording" | "working">("idle");
  const [note, setNote] = useState("");
  // Asked up front, not after a failed recording. The app ships neither transcriber: the local
  // model is ~300 MB of native code (most of it a video codec suite, to decode a microphone), and
  // the hosted route needs an OpenAI key specifically — a key for another provider does not help,
  // so "add an API key" would send someone to add the wrong one.
  const support = useQuery({ queryKey: ["dictation"], queryFn: getDictationSupport });
  const unavailable = support.data?.support === "no";

  async function start() {
    setNote("");
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      // Refused permission, or no microphone. Both are "we cannot hear you", and neither is an
      // error the user needs a stack trace for.
      setNote(t("code.dictate.noMic"));
      return;
    }
    chunks.current = [];
    const rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => chunks.current.push(e.data);
    rec.onstop = () => {
      // Release the microphone the moment we stop needing it. A tab holding an open mic is the kind
      // of thing that shows up as a recording indicator someone cannot explain.
      for (const track of stream.getTracks()) track.stop();
      void send(new Blob(chunks.current, { type: "audio/webm" }));
    };
    recorder.current = rec;
    rec.start();
    setState("recording");
  }

  async function send(audio: Blob) {
    setState("working");
    // The first recording on a machine downloads the speech model — a few hundred megabytes, on a
    // click that promised to type a sentence. The app ships the transcriber but not the weights, so
    // this wait exists exactly once and looks identical to a hang if nobody says why.
    setNote(t("code.dictate.working"));
    try {
      const result = await transcribe(audio);
      if (result.text) {
        onText(result.text);
        setNote("");
      } else {
        setNote(result.note || t("code.dictate.nothing"));
      }
    } catch {
      setNote(t("code.dictate.failed"));
    }
    setState("idle");
  }

  function stop() {
    recorder.current?.stop();
    recorder.current = null;
  }

  return (
    <>
      <Button
        size="sm"
        variant={state === "recording" ? "primary" : "ghost"}
        disabled={state === "working" || unavailable}
        title={unavailable ? t("code.dictate.unavailable") : t("code.dictate.hint")}
        aria-pressed={state === "recording"}
        onClick={() => (state === "recording" ? stop() : void start())}
      >
        {state === "working" ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : state === "recording" ? (
          <Square className="h-4 w-4" />
        ) : (
          <Mic className="h-4 w-4" />
        )}
        {t(state === "recording" ? "code.dictate.stop" : "code.dictate.label")}
      </Button>
      {unavailable ? (
        <span className="text-xs text-muted-foreground">{t("code.dictate.unavailable")}</span>
      ) : note ? (
        <span className="text-xs text-warn">{note}</span>
      ) : null}
    </>
  );
}
