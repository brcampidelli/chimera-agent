import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BrowserSpeaker, pickVoice, VOICES_WAIT_MS } from "@/lib/voice/speaker";

/**
 * The voice that reads, chosen from what the window offers — measured lists, not guessed ones.
 * `EDGE` is the order Edge on Windows returned on 2026-09-17 (trimmed from 323): the operating
 * system's own two voices first, the neural "Online (Natural)" ones far below. `CHROME` is
 * Chrome on the same machine: the same two, plus Google's online voice.
 */

function voice(name: string, lang: string, localService: boolean): SpeechSynthesisVoice {
  return { name, lang, localService, default: false, voiceURI: name } as SpeechSynthesisVoice;
}

const EDGE = [
  voice("Microsoft Daniel - Portuguese (Brazil)", "pt-BR", true),
  voice("Microsoft Maria - Portuguese (Brazil)", "pt-BR", true),
  voice("Microsoft Adri Online (Natural) - Afrikaans (South Africa)", "af-ZA", false),
  voice("Microsoft Ava Online (Natural) - English (United States)", "en-US", false),
  voice("Microsoft Francisca Online (Natural) - Portuguese (Brazil)", "pt-BR", false),
  voice("Microsoft Antônio Online (Natural) - Portuguese (Brazil)", "pt-BR", false),
  voice("Microsoft Raquel Online (Natural) - Portuguese (Portugal)", "pt-PT", false),
];
const CHROME = [
  voice("Microsoft Daniel - Portuguese (Brazil)", "pt-BR", true),
  voice("Microsoft Maria - Portuguese (Brazil)", "pt-BR", true),
  voice("Google português do Brasil", "pt-BR", false),
  voice("Google US English", "en-US", false),
];

describe("the voice that reads", () => {
  it("is the neural one in Edge, not the first one with the right tag", () => {
    expect(pickVoice(EDGE, "pt-BR")?.name).toContain("Francisca Online (Natural)");
    expect(pickVoice(EDGE, "en-US")?.name).toContain("Ava Online (Natural)");
  });

  it("is Google's online voice in Chrome, over the operating system's", () => {
    expect(pickVoice(CHROME, "pt-BR")?.name).toBe("Google português do Brasil");
  });

  it("is the one chosen by name while the window still has it, and the ranked pick otherwise", () => {
    expect(pickVoice(EDGE, "pt-BR", "Microsoft Maria - Portuguese (Brazil)")?.name).toContain("Maria");
    expect(pickVoice(EDGE, "pt-BR", "Microsoft Thalita multilíngue Online (Natural) - Portuguese (Brazil)")?.name).toContain("Francisca");
  });

  it("falls back to the same language, then to the engine's default", () => {
    // No pt-BR at all: Portugal's neural voice rather than nothing.
    const noBrazil = EDGE.filter((v) => v.lang !== "pt-BR");
    expect(pickVoice(noBrazil, "pt-BR")?.name).toContain("Raquel");
    // Only the operating system's voices: still a Portuguese one, not silence.
    expect(pickVoice(EDGE.slice(0, 2), "pt-BR")?.name).toContain("Daniel");
    expect(pickVoice(EDGE, "ja-JP")).toBeNull();
    expect(pickVoice([], "pt-BR")).toBeNull();
  });
});

/** A `speechSynthesis` whose list is empty until `voiceschanged`, like a fresh document's. */
class FakeSynthesis {
  spoken: SpeechSynthesisUtterance[] = [];
  cancelled = 0;
  list: SpeechSynthesisVoice[] = [];
  private listeners: Array<() => void> = [];
  readonly speaking = false;
  getVoices() {
    return this.list;
  }
  addEventListener(_type: "voiceschanged", listener: () => void) {
    this.listeners.push(listener);
  }
  fill(voices: SpeechSynthesisVoice[]) {
    this.list = voices;
    const listeners = this.listeners;
    this.listeners = [];
    for (const l of listeners) l();
  }
  speak(utterance: SpeechSynthesisUtterance) {
    this.spoken.push(utterance);
  }
  cancel() {
    this.cancelled += 1;
  }
  /** The engine finished reading piece `i`. */
  end(i: number) {
    this.spoken[i].onend?.(new Event("end") as SpeechSynthesisEvent);
  }
}

class FakeUtterance {
  lang = "";
  voice: SpeechSynthesisVoice | null = null;
  onend: ((ev: SpeechSynthesisEvent) => void) | null = null;
  onerror: ((ev: SpeechSynthesisErrorEvent) => void) | null = null;
  constructor(public text: string) {}
}

describe("the browser speaker", () => {
  let synth: FakeSynthesis;
  beforeEach(() => {
    synth = new FakeSynthesis();
    vi.stubGlobal("speechSynthesis", synth);
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("waits for the voice list to fill before reading, and then reads with the neural voice", async () => {
    const speaker = new BrowserSpeaker();
    const reading = speaker.speak("Olá.", "pt-BR");
    expect(speaker.speaking()).toBe(true);
    expect(synth.spoken).toHaveLength(0); // not yet: the list is empty
    synth.fill(EDGE);
    await vi.waitFor(() => expect(synth.spoken).toHaveLength(1));
    expect(synth.spoken[0].voice?.name).toContain("Francisca");
    expect(synth.spoken[0].lang).toBe("pt-BR");
    synth.end(0);
    await reading;
    expect(speaker.speaking()).toBe(false);
  });

  it("gives up waiting after a while and reads with the default voice, once", async () => {
    vi.useFakeTimers();
    try {
      const speaker = new BrowserSpeaker();
      void speaker.speak("Hello.", "en-US");
      await vi.advanceTimersByTimeAsync(VOICES_WAIT_MS + 1);
      expect(synth.spoken).toHaveLength(1);
      expect(synth.spoken[0].voice).toBeNull();
      // The second piece does not wait again.
      void speaker.speak("Again.", "en-US");
      await vi.advanceTimersByTimeAsync(0);
      expect(synth.spoken).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("queues pieces in order, is busy until the last ends, and a cancel drops them all", async () => {
    synth.fill(EDGE);
    const speaker = new BrowserSpeaker();
    const first = speaker.speak("One.", "pt-BR");
    const second = speaker.speak("Two.", "pt-BR");
    await vi.waitFor(() => expect(synth.spoken.map((u) => u.text)).toEqual(["One.", "Two."]));
    let idle = false;
    void speaker.idle().then(() => (idle = true));
    synth.end(0);
    await first;
    expect(speaker.speaking()).toBe(true);
    expect(idle).toBe(false);
    synth.end(1);
    await second;
    await vi.waitFor(() => expect(idle).toBe(true));
    expect(speaker.speaking()).toBe(false);

    const third = speaker.speak("Three.", "pt-BR");
    const fourth = speaker.speak("Four.", "pt-BR");
    await vi.waitFor(() => expect(synth.spoken).toHaveLength(4));
    speaker.cancel();
    expect(synth.cancelled).toBe(1);
    expect(speaker.speaking()).toBe(false);
    await Promise.all([third, fourth, speaker.idle()]);
    // The engine's late `error` for the cancelled pieces changes nothing.
    synth.spoken[2].onerror?.(new Event("error") as SpeechSynthesisErrorEvent);
    expect(speaker.speaking()).toBe(false);
  });
});
