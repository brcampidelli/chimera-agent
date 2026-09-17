import { useQuery } from "@tanstack/react-query";
import { Ear, Loader2, Mic, Volume2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { getDictationSupport, transcribe as transcribeAudio, type Transcript } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { BrowserMicrophone, FRAME_MS, type MicrophoneLike } from "@/lib/voice/microphone";
import { Segmenter, rmsOf } from "@/lib/voice/segmenter";
import { BrowserSpeaker, type SpeakerLike } from "@/lib/voice/speaker";
import { plainForSpeech, speechLocale } from "@/lib/voice/speech-text";
import { encodeWav } from "@/lib/voice/wav";

/**
 * Hands-free: listen, send what was said, read the answer aloud, and stop reading when the person
 * speaks over it.
 *
 * The dictation button next door is push-to-talk: record, stop, transcribe, paste. This is the
 * continuous form of the same three parts. The microphone stays open; a segmenter cuts the stream
 * into utterances on silence; each utterance is transcribed through the same endpoint dictation
 * uses and sent as a turn; when the turn's answer lands it is read aloud with the window's own
 * voices; and while it is being read, speech above the agent's own voice cancels the reading —
 * barge-in — and becomes the next message.
 *
 * What the screen says is what the machine is doing: listening, hearing, transcribing (with how
 * long it took, because the local model on a slow machine takes seconds and a status line that
 * hides that reads as a hang), reading aloud. The mode is never remembered across launches: an
 * app that opens the microphone on its own at startup is not a thing anyone asked for.
 *
 * Everything with a device behind it is injectable (`deps`), so the state machine is tested with
 * a fake microphone and a fake voice; the real ones are the defaults.
 */

export interface VoiceModeDeps {
  microphone: () => MicrophoneLike;
  speaker: SpeakerLike;
  transcribe: (audio: Blob, filename: string) => Promise<Transcript>;
  segmenter: () => Segmenter;
}

const DEFAULT_DEPS: VoiceModeDeps = {
  microphone: () => new BrowserMicrophone(),
  speaker: new BrowserSpeaker(),
  transcribe: transcribeAudio,
  segmenter: () => new Segmenter({ frameMs: FRAME_MS }),
};

export type VoicePhase = "off" | "listening" | "hearing" | "transcribing" | "speaking";

export interface SpokenAnswer {
  /** Changes when a new answer lands; the same text twice is two answers. */
  seq: number;
  text: string;
}

export function VoiceMode({
  onUtterance,
  answer,
  deps = DEFAULT_DEPS,
}: {
  /** What the person said, transcribed — the caller sends it as a turn. */
  onUtterance: (text: string) => void;
  /** The latest finished answer, or null. Read aloud when its `seq` moves while the mode is on. */
  answer: SpokenAnswer | null;
  deps?: VoiceModeDeps;
}) {
  const { t, lang } = useI18n();
  const [phase, setPhase] = useState<VoicePhase>("off");
  const [note, setNote] = useState("");
  const [heard, setHeard] = useState<{ text: string; seconds: number } | null>(null);
  const support = useQuery({ queryKey: ["dictation"], queryFn: getDictationSupport });
  const unavailable = support.data?.support === "no";

  const mic = useRef<MicrophoneLike | null>(null);
  const segmenter = useRef<Segmenter | null>(null);
  const phaseRef = useRef<VoicePhase>("off");
  /** The answer seq already spoken (or current when the mode came on): never read old answers. */
  const spokenSeq = useRef<number>(answer?.seq ?? -1);
  const onUtteranceRef = useRef(onUtterance);
  onUtteranceRef.current = onUtterance;
  const locale = useMemo(() => speechLocale(lang), [lang]);

  const setPhaseBoth = useCallback((next: VoicePhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const stop = useCallback(() => {
    mic.current?.stop();
    mic.current = null;
    deps.speaker.cancel();
    segmenter.current?.flush();
    segmenter.current = null;
    setPhaseBoth("off");
  }, [deps.speaker, setPhaseBoth]);

  // The mic is released when the screen goes away, whatever the mode said.
  useEffect(() => () => stop(), [stop]);

  const handleUtterance = useCallback(
    async (samples: Float32Array, sampleRate: number) => {
      setPhaseBoth("transcribing");
      const began = performance.now();
      try {
        const result = await deps.transcribe(encodeWav(samples, sampleRate), "speech.wav");
        const seconds = Math.round((performance.now() - began) / 100) / 10;
        if (result.text) {
          setHeard({ text: result.text, seconds });
          setNote("");
          onUtteranceRef.current(result.text);
        } else {
          setNote(result.note || t("code.dictate.nothing"));
        }
      } catch {
        setNote(t("code.dictate.failed"));
      }
      if (phaseRef.current === "transcribing") setPhaseBoth("listening");
    },
    [deps, setPhaseBoth, t],
  );

  const start = useCallback(async () => {
    setNote("");
    setHeard(null);
    const microphone = deps.microphone();
    const seg = deps.segmenter();
    segmenter.current = seg;
    spokenSeq.current = answer?.seq ?? -1;
    try {
      await microphone.start((frame) => {
        const event = seg.feed(rmsOf(frame), frame);
        if (!event) return;
        if (event.kind === "start") {
          // Barge-in: the person is talking over the reading. Stop it — the segmenter's floor has
          // already climbed to the agent's own voice, so this frame was louder than that.
          if (deps.speaker.speaking()) {
            deps.speaker.cancel();
            seg.agentSpeaking = false;
            setNote(t("code.voice.interrupted"));
          }
          setPhaseBoth("hearing");
        } else if (event.kind === "end") {
          void handleUtterance(event.samples, microphone.sampleRate);
        } else if (phaseRef.current === "hearing") {
          setPhaseBoth("listening");
        }
      });
    } catch {
      setNote(t("code.dictate.noMic"));
      segmenter.current = null;
      return;
    }
    mic.current = microphone;
    setPhaseBoth("listening");
    if (!deps.speaker.available()) setNote(t("code.voice.noSpeech"));
  }, [answer?.seq, deps, handleUtterance, setPhaseBoth, t]);

  // A new answer while the mode is on is read aloud. The segmenter is told, so its floor tracks
  // the agent's voice and only speech above it counts as an interruption.
  useEffect(() => {
    if (phaseRef.current === "off" || !answer || answer.seq === spokenSeq.current) return;
    spokenSeq.current = answer.seq;
    if (!deps.speaker.available() || !answer.text.trim()) return;
    const seg = segmenter.current;
    void (async () => {
      if (seg) seg.agentSpeaking = true;
      setPhaseBoth("speaking");
      await deps.speaker.speak(plainForSpeech(answer.text, t("code.voice.codeMarker")), locale);
      if (seg) seg.agentSpeaking = false;
      // Still "speaking" means nothing else moved the phase (a stop sets "off", a barge-in
      // "hearing"); the reading ended on its own and the mode goes back to listening.
      if (phaseRef.current === "speaking") setPhaseBoth("listening");
    })();
  }, [answer, deps.speaker, locale, setPhaseBoth, t]);

  const on = phase !== "off";
  const status =
    phase === "listening"
      ? t("code.voice.listening")
      : phase === "hearing"
        ? t("code.voice.hearing")
        : phase === "transcribing"
          ? t("code.voice.transcribing")
          : phase === "speaking"
            ? t("code.voice.speaking")
            : "";

  return (
    <>
      <Button
        size="sm"
        variant={on ? "primary" : "ghost"}
        disabled={unavailable}
        title={unavailable ? t("code.dictate.unavailable") : t("code.voice.hint")}
        aria-pressed={on}
        data-testid="voice-mode"
        onClick={() => (on ? stop() : void start())}
      >
        {phase === "transcribing" ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : phase === "speaking" ? (
          <Volume2 className="h-4 w-4" />
        ) : phase === "hearing" ? (
          <Ear className="h-4 w-4" />
        ) : (
          <Mic className="h-4 w-4" />
        )}
        {t("code.voice.label")}
      </Button>
      {on ? (
        <span className="text-xs text-muted-foreground" role="status" data-testid="voice-status">
          {status}
          {heard ? ` · ${t("code.voice.heard", { s: heard.seconds, text: heard.text.slice(0, 80) })}` : ""}
        </span>
      ) : null}
      {note ? (
        <span className="text-xs text-muted-foreground" data-testid="voice-note">
          {note}
        </span>
      ) : null}
    </>
  );
}
