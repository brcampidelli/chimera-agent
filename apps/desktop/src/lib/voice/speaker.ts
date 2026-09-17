/**
 * Reading an answer aloud with the voices the window already has.
 *
 * `speechSynthesis` — the browser's own text-to-speech, backed by the operating system's voices
 * in the desktop's WebView — rather than a hosted voice API. It needs no key, no download and no
 * network, it works in every language the app is translated into, and it can be cancelled in the
 * middle of a word, which is what barge-in is. A hosted voice would sound better and cost money
 * and a round trip; that is a different feature and a later one.
 *
 * Wrapped so the voice mode can be tested with a fake, and so absence is an answer rather than a
 * crash: a window without `speechSynthesis` (the test runner; a stripped WebView) reads nothing,
 * and the mode says so on screen instead of pretending.
 */

export interface SpeakerLike {
  available(): boolean;
  /** Speak `text`; resolves when the reading ends or is cancelled. Never rejects. */
  speak(text: string, locale: string): Promise<void>;
  cancel(): void;
  speaking(): boolean;
}

interface SynthesisLike {
  speak(utterance: SpeechSynthesisUtterance): void;
  cancel(): void;
  getVoices(): SpeechSynthesisVoice[];
  readonly speaking: boolean;
}

function synthesis(): SynthesisLike | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { speechSynthesis?: SynthesisLike; SpeechSynthesisUtterance?: unknown };
  if (!w.speechSynthesis || typeof w.SpeechSynthesisUtterance !== "function") return null;
  return w.speechSynthesis;
}

/** The best voice for a locale: exact tag first, then the same language, else the default. */
export function pickVoice(voices: SpeechSynthesisVoice[], locale: string): SpeechSynthesisVoice | null {
  const norm = (tag: string) => tag.toLowerCase().replace("_", "-");
  const wanted = norm(locale);
  const exact = voices.find((v) => norm(v.lang) === wanted);
  if (exact) return exact;
  const language = wanted.split("-")[0];
  const same = voices.find((v) => norm(v.lang).split("-")[0] === language);
  return same ?? null;
}

export class BrowserSpeaker implements SpeakerLike {
  private active = false;

  available(): boolean {
    return synthesis() !== null;
  }

  speaking(): boolean {
    return this.active;
  }

  speak(text: string, locale: string): Promise<void> {
    const synth = synthesis();
    if (!synth || !text.trim()) return Promise.resolve();
    return new Promise((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = locale;
      const voice = pickVoice(synth.getVoices(), locale);
      if (voice) utterance.voice = voice;
      const done = () => {
        this.active = false;
        resolve();
      };
      utterance.onend = done;
      // A cancel fires `error` with `interrupted`/`canceled` — the same ending for the caller.
      utterance.onerror = done;
      this.active = true;
      synth.speak(utterance);
    });
  }

  cancel(): void {
    const synth = synthesis();
    if (!synth) return;
    synth.cancel();
    this.active = false;
  }
}
