/**
 * Reading an answer aloud with the voices the window already has.
 *
 * `speechSynthesis` — the browser's own text-to-speech, backed by the operating system's voices
 * in the desktop's WebView — rather than a hosted voice API. It needs no key, no download and no
 * network, it works in every language the app is translated into, and it can be cancelled in the
 * middle of a word, which is what barge-in is. A hosted voice would sound better and cost money
 * and a round trip; that is a different feature and a later one.
 *
 * Two things the first live test taught (2026-09-17, Edge on Windows, 323 voices). The list is
 * empty on the very first `getVoices()` of a document and fills a moment later — a reading that
 * does not wait is read by the default voice. And the list puts the operating system's own
 * voices first ("Microsoft Daniel", "Microsoft Maria", 2010-era concatenative), so "the first
 * voice with the right tag" is the robotic one, with "Microsoft Francisca Online (Natural)" three
 * hundred entries down. The picker ranks; the reader waits.
 *
 * Wrapped so the voice mode can be tested with a fake, and so absence is an answer rather than a
 * crash: a window without `speechSynthesis` (the test runner; a stripped WebView) reads nothing,
 * and the mode says so on screen instead of pretending.
 */

import { LANGS, type Lang } from "@/lib/i18n";

export interface SpeakerLike {
  available(): boolean;
  /** Queue `text` after whatever is being read; resolves when this piece ends or is cancelled.
   *  Never rejects. */
  speak(text: string, locale: string): Promise<void>;
  /** Stop, and drop everything queued. */
  cancel(): void;
  /** Something is being read or waiting to be. */
  speaking(): boolean;
  /** Resolves when nothing is being read and nothing is queued. */
  idle(): Promise<void>;
}

interface SynthesisLike {
  speak(utterance: SpeechSynthesisUtterance): void;
  cancel(): void;
  getVoices(): SpeechSynthesisVoice[];
  readonly speaking: boolean;
  addEventListener?(type: "voiceschanged", listener: () => void, options?: { once?: boolean }): void;
}

function synthesis(): SynthesisLike | null {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { speechSynthesis?: SynthesisLike; SpeechSynthesisUtterance?: unknown };
  if (!w.speechSynthesis || typeof w.SpeechSynthesisUtterance !== "function") return null;
  return w.speechSynthesis;
}

/** How long the reader waits for the voice list to fill, the first time. */
export const VOICES_WAIT_MS = 1500;

/** What a voice is worth, higher first. The names are the browsers' own conventions: Edge and
 *  Windows call their neural voices "(Natural)", Apple's better ones are "Premium"/"Enhanced",
 *  Chrome's online ones are "Google …"; an online voice is, in practice, a neural one. */
export function voiceScore(voice: SpeechSynthesisVoice): number {
  const name = voice.name.toLowerCase();
  let score = 0;
  if (/natural|neural/.test(name)) score += 4;
  if (/premium|enhanced|siri/.test(name)) score += 3;
  if (/google|online/.test(name)) score += 2;
  if (!voice.localService) score += 1;
  return score;
}

/** Where this window keeps the voice the person chose in Settings (a name; "" is automatic).
 *  Per window on purpose: the voices are this machine's, and a name means nothing elsewhere. */
export const VOICE_KEY = "chimera.voice";

export function preferredVoiceName(): string {
  try {
    return localStorage.getItem(VOICE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setPreferredVoiceName(name: string): void {
  try {
    if (name) localStorage.setItem(VOICE_KEY, name);
    else localStorage.removeItem(VOICE_KEY);
  } catch {
    // a window that will not remember still reads, with the automatic pick
  }
}

/** Where this window keeps how fast the answers are read — a multiple of the engine's own pace.
 *  Per window for the same reason as the voice: it goes with the voice, not with the account. */
export const VOICE_RATE_KEY = "chimera.voiceRate";

/** The paces offered. Four steps rather than a slider: a rate between these is not something
 *  anyone hears the difference of, and a slider invites 0.93. 1 is the engine's normal pace and
 *  what every window reads at until someone picks another. */
export const VOICE_RATES = [0.8, 1, 1.25, 1.5] as const;

export function preferredVoiceRate(): number {
  try {
    const stored = Number(localStorage.getItem(VOICE_RATE_KEY));
    // Only an offered step: a value edited by hand to 9 would read faster than anyone can follow.
    return (VOICE_RATES as readonly number[]).includes(stored) ? stored : 1;
  } catch {
    return 1;
  }
}

export function setPreferredVoiceRate(rate: number): void {
  try {
    if (rate === 1) localStorage.removeItem(VOICE_RATE_KEY);
    else localStorage.setItem(VOICE_RATE_KEY, String(rate));
  } catch {
    // a window that will not remember still reads, at the normal pace
  }
}

/** Where this window keeps the language the voice speaks and listens in (a two-letter code; ""
 *  is the interface's). Separate from the interface because the two are different questions: an
 *  app read in English by someone who dictates in Portuguese is an ordinary setup. */
export const VOICE_LANG_KEY = "chimera.voiceLang";

/** The language chosen in Settings while the app still offers it, or "" — follow the interface.
 *  What the Settings card shows; the voice itself asks `preferredVoiceLang`. */
export function chosenVoiceLang(): Lang | "" {
  try {
    const stored = localStorage.getItem(VOICE_LANG_KEY) ?? "";
    return LANGS.find((l) => l.code === stored)?.code ?? "";
  } catch {
    return "";
  }
}

/** The language the voice uses: the chosen one, otherwise the interface's — which is what every
 *  window did before there was a choice. */
export function preferredVoiceLang(appLang: Lang): Lang {
  return chosenVoiceLang() || appLang;
}

export function setPreferredVoiceLang(code: Lang | ""): void {
  try {
    if (code) localStorage.setItem(VOICE_LANG_KEY, code);
    else localStorage.removeItem(VOICE_LANG_KEY);
  } catch {
    // a window that will not remember follows the interface
  }
}

const sameLanguage = (tag: string, locale: string) =>
  tag.toLowerCase().replace("_", "-").split("-")[0] === locale.toLowerCase().split("-")[0];

/** The voices this window offers for a locale's language, in the engine's order. */
export function voicesOf(locale: string): SpeechSynthesisVoice[] {
  const synth = synthesis();
  if (!synth) return [];
  return synth.getVoices().filter((v) => sameLanguage(v.lang, locale));
}

/** The voice that reads: the one chosen by name when the window still has it, else the
 *  highest-scoring voice with the exact tag, else the highest-scoring one of the same language,
 *  else none (the engine's default then reads). Ties keep the list's order.
 *
 *  `sameLanguageOnly`: honour the named voice only while it speaks the language being read. Set
 *  when a voice language was chosen in Settings — then the language is a decision, and a
 *  Portuguese voice chosen before it became English would read English with a Portuguese mouth
 *  while the card (which lists that language's voices only) says "Automatic" over it. Unset, the
 *  named voice reads whatever its language, as it always did: an Edge "Multilingual" voice
 *  declares en-US and reads Portuguese well, and someone who picked one and then changed the
 *  interface's language keeps hearing it. */
export function pickVoice(
  voices: SpeechSynthesisVoice[],
  locale: string,
  preferred = "",
  sameLanguageOnly = false,
): SpeechSynthesisVoice | null {
  if (preferred) {
    const named = voices.find(
      (v) => v.name === preferred && (!sameLanguageOnly || sameLanguage(v.lang, locale)),
    );
    if (named) return named;
  }
  const norm = (tag: string) => tag.toLowerCase().replace("_", "-");
  const wanted = norm(locale);
  const language = wanted.split("-")[0];
  const best = (candidates: SpeechSynthesisVoice[]) =>
    candidates.reduce<SpeechSynthesisVoice | null>(
      (top, v) => (top === null || voiceScore(v) > voiceScore(top) ? v : top),
      null,
    );
  return (
    best(voices.filter((v) => norm(v.lang) === wanted)) ??
    best(voices.filter((v) => norm(v.lang).split("-")[0] === language))
  );
}

export class BrowserSpeaker implements SpeakerLike {
  private pending = 0;
  private waiters: Array<() => void> = [];
  /** One settler per piece queued, so a cancel can end them all without waiting for the engine. */
  private settlers: Array<() => void> = [];
  /** The voice list was asked for and came back empty; wait for it once, not on every piece. */
  private voicesWaited = false;

  available(): boolean {
    const synth = synthesis();
    // Asking is what starts the list loading, so the first reading finds it filled.
    synth?.getVoices();
    return synth !== null;
  }

  speaking(): boolean {
    return this.pending > 0;
  }

  idle(): Promise<void> {
    if (this.pending === 0) return Promise.resolve();
    return new Promise((resolve) => this.waiters.push(resolve));
  }

  private voices(synth: SynthesisLike): Promise<SpeechSynthesisVoice[]> {
    const now = synth.getVoices();
    if (now.length || this.voicesWaited || !synth.addEventListener) return Promise.resolve(now);
    this.voicesWaited = true;
    return new Promise((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve(synth.getVoices());
      };
      synth.addEventListener?.("voiceschanged", finish, { once: true });
      setTimeout(finish, VOICES_WAIT_MS);
    });
  }

  speak(text: string, locale: string): Promise<void> {
    const synth = synthesis();
    if (!synth || !text.trim()) return Promise.resolve();
    this.pending += 1;
    return new Promise((resolve) => {
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        this.pending = Math.max(0, this.pending - 1);
        if (this.pending === 0) this.drain();
        resolve();
      };
      void this.voices(synth).then((voices) => {
        // Cancelled while the voice list was still loading: nothing to read any more.
        if (settled) return;
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = locale;
        utterance.rate = preferredVoiceRate();
        // Strict about the voice's language only once one was chosen — before that, the named
        // voice reads as it did before there was a choice.
        const voice = pickVoice(voices, locale, preferredVoiceName(), chosenVoiceLang() !== "");
        if (voice) utterance.voice = voice;
        utterance.onend = done;
        // A cancel fires `error` with `interrupted`/`canceled` — the same ending for the caller.
        utterance.onerror = done;
        synth.speak(utterance);
      });
      this.settlers.push(done);
    });
  }

  private drain(): void {
    const waiters = this.waiters;
    this.waiters = [];
    this.settlers = [];
    for (const resolve of waiters) resolve();
  }

  cancel(): void {
    const synth = synthesis();
    if (!synth) return;
    synth.cancel();
    // Settle every piece now: the engine's own `error` events arrive later and find them settled.
    // The last one to settle brings `pending` to zero and releases whoever waits on `idle()`.
    const settlers = this.settlers;
    this.settlers = [];
    for (const settle of settlers) settle();
    this.pending = 0;
  }
}
