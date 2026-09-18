import { useQuery } from "@tanstack/react-query";
import { Ear, Loader2, Mic, Volume2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { getDictationSupport, transcribe as transcribeAudio, warmTranscriber, type Transcript } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { BrowserMicrophone, FRAME_MS, type MicrophoneLike } from "@/lib/voice/microphone";
import { Segmenter, rmsOf } from "@/lib/voice/segmenter";
import { BrowserSpeaker, type SpeakerLike } from "@/lib/voice/speaker";
import { countSentences, firstSentences, plainForSpeech, readyCut, screenPartStart, speechLocale } from "@/lib/voice/speech-text";
import { encodeWav } from "@/lib/voice/wav";

/**
 * Hands-free: listen, send what was said, read the answer aloud, and stop reading when the person
 * speaks over it.
 *
 * The dictation button next door is push-to-talk: record, stop, transcribe, paste. This is the
 * continuous form of the same three parts. The microphone stays open; a segmenter cuts the stream
 * into utterances on silence; each utterance is transcribed through the same endpoint dictation
 * uses and sent as a turn — marked as spoken, so the model answers for the ear; the answer is
 * read aloud with the window's own voices AS IT STREAMS, a sentence at a time; and while it is
 * being read, speech above the agent's own voice cancels the reading — barge-in — and becomes the
 * next message.
 *
 * Reading as it streams is what the first live test asked for without saying so: the answer was
 * read only once it was complete, and a complete answer at twenty tokens a second is a quarter of
 * a minute of silence. Now each sentence is queued the moment it is complete (`readyCut`). The
 * reading stops at the line the model was told to put between the spoken part and the screen part
 * (`---`), and, as a net under a model that ignores the instruction, after `MAX_SPOKEN_SENTENCES`
 * — either way the voice says the rest is on the screen.
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
  transcribe: (audio: Blob, filename: string, language: string) => Promise<Transcript>;
  /** Load the speech model now: the first utterance is seconds away. */
  warm: () => Promise<void>;
  segmenter: () => Segmenter;
}

/** The most sentences read from one answer before the voice says the rest is on the screen. A
 *  net, not the rule: the model is asked (`spoken`) to answer in two to four sentences and to put
 *  anything longer under a `---` line, where the reading stops on its own. Six sentences is
 *  thirty to forty seconds of listening. */
export const MAX_SPOKEN_SENTENCES = 6;

const DEFAULT_DEPS: VoiceModeDeps = {
  microphone: () => new BrowserMicrophone(),
  speaker: new BrowserSpeaker(),
  transcribe: transcribeAudio,
  warm: warmTranscriber,
  segmenter: () => new Segmenter({ frameMs: FRAME_MS }),
};

export type VoicePhase = "off" | "listening" | "hearing" | "transcribing" | "speaking";

export interface SpokenAnswer {
  /** Changes when a new answer begins; the same text twice is two answers. */
  seq: number;
  /** The answer so far — it grows while the turn streams. */
  text: string;
  /** Nothing more is coming: the turn ended, however it ended. */
  done: boolean;
}

/** How much of the answer being read has been handed to the voice, and what stopped it. */
interface Reading {
  seq: number;
  /** Characters of the raw answer already queued for reading. */
  queued: number;
  sentences: number;
  /** The reading was cut short — by the cap, by the `---` line, or by the person — and nothing
   *  more of this answer is read. */
  closed: boolean;
  /** "The rest is on the screen" was said (or there was no rest to speak of). */
  restSaid: boolean;
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
  const reading = useRef<Reading | null>(null);
  const onUtteranceRef = useRef(onUtterance);
  onUtteranceRef.current = onUtterance;
  const locale = useMemo(() => speechLocale(lang), [lang]);

  const setPhaseBoth = useCallback((next: VoicePhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  /** End the reading of the current answer for good: nothing more of it is queued. */
  const closeReading = useCallback(() => {
    deps.speaker.cancel();
    if (reading.current) reading.current.closed = true;
    if (segmenter.current) segmenter.current.agentSpeaking = false;
  }, [deps.speaker]);

  const stop = useCallback(() => {
    mic.current?.stop();
    mic.current = null;
    closeReading();
    segmenter.current?.flush();
    segmenter.current = null;
    setPhaseBoth("off");
  }, [closeReading, setPhaseBoth]);

  // The mic is released when the screen goes away, whatever the mode said.
  useEffect(() => () => stop(), [stop]);

  const handleUtterance = useCallback(
    async (samples: Float32Array, sampleRate: number) => {
      setPhaseBoth("transcribing");
      const began = performance.now();
      try {
        const result = await deps.transcribe(encodeWav(samples, sampleRate), "speech.wav", lang);
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
    [deps, lang, setPhaseBoth, t],
  );

  const start = useCallback(async () => {
    setNote("");
    setHeard(null);
    // The model loads while the person draws breath, not on their first sentence.
    void deps.warm();
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
          // already climbed to the agent's own voice, so this frame was louder than that. The
          // pause between two sentences of a streaming answer counts as the reading too: a person
          // who speaks into it does not want the next sentence.
          if (deps.speaker.speaking() || phaseRef.current === "speaking") {
            closeReading();
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
  }, [answer?.seq, closeReading, deps, handleUtterance, setPhaseBoth, t]);

  // An answer that begins while the mode is on is read aloud as it streams: every complete
  // sentence is queued the moment it is complete, and the remainder when the turn ends. The
  // segmenter is told, so its floor tracks the agent's voice and only speech above it counts as
  // an interruption.
  useEffect(() => {
    if (phaseRef.current === "off" || !answer || !deps.speaker.available()) return;
    if (answer.seq !== reading.current?.seq) {
      // An answer that was current when the mode came on is never read.
      if (answer.seq === spokenSeq.current) return;
      spokenSeq.current = answer.seq;
      reading.current = { seq: answer.seq, queued: 0, sentences: 0, closed: false, restSaid: false };
    }
    const piece = reading.current;
    if (piece.closed) return;
    const seg = segmenter.current;
    const raw = answer.text;
    // The spoken part ends at the model's own line; the screen part is never read.
    const screenAt = screenPartStart(raw);
    const spokenEnd = screenAt >= 0 ? screenAt : raw.length;
    const cut = answer.done || screenAt >= 0 ? spokenEnd : readyCut(raw, piece.queued);
    const queue = (text: string) => {
      if (seg) seg.agentSpeaking = true;
      setPhaseBoth("speaking");
      void deps.speaker.speak(text, locale);
    };
    const close = (rest: boolean) => {
      piece.closed = true;
      if (rest && !piece.restSaid) {
        piece.restSaid = true;
        queue(t("code.voice.restOnScreen"));
      }
    };
    if (cut > piece.queued) {
      const text = plainForSpeech(raw.slice(piece.queued, cut), t("code.voice.codeMarker"));
      piece.queued = cut;
      if (text) {
        const { kept, truncated } = firstSentences(text, MAX_SPOKEN_SENTENCES - piece.sentences);
        piece.sentences += countSentences(kept);
        if (kept) queue(kept);
        // Over the cap: this piece (or what is left of it) is the rest, and nothing more is read.
        if (truncated) close(true);
      }
    }
    // The rule line itself is not "the rest"; what follows it is.
    if (!piece.closed && screenAt >= 0) {
      close(raw.slice(screenAt).split("\n").slice(1).join("\n").trim() !== "");
    }
    // Nothing more will be queued for this answer: when the voice falls silent, listen again.
    if ((answer.done || piece.closed) && phaseRef.current === "speaking") {
      const seq = piece.seq;
      void deps.speaker.idle().then(() => {
        if (reading.current?.seq !== seq || phaseRef.current !== "speaking") return;
        if (seg) seg.agentSpeaking = false;
        setPhaseBoth("listening");
      });
    }
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
