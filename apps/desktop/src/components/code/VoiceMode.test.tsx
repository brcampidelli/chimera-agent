import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MAX_SPOKEN_SENTENCES, VoiceMode, type SpokenAnswer, type VoiceModeDeps } from "@/components/code/VoiceMode";
import { getDictationSupport, type Transcript } from "@/lib/api";
import { DEFAULT_SEGMENTER, Segmenter } from "@/lib/voice/segmenter";
import type { FrameSink, MicrophoneLike } from "@/lib/voice/microphone";
import type { SpeakerLike } from "@/lib/voice/speaker";
import { renderWithProviders } from "@/test/utils";

/**
 * The hands-free state machine, driven with a fake microphone and a fake voice.
 *
 * What is pinned: switching the mode on opens the mic and says "listening"; an utterance is
 * transcribed — with the app's language as the hint — and handed to the caller as a message, with
 * how long the transcription took; an answer that begins while the mode is on is read aloud as it
 * streams, a sentence at a time and stripped of its Markdown, the first sentence before the turn
 * is done; the reading stops at the model's `---` line and after the sentence cap, saying the rest
 * is on the screen; speech over the reading cancels it (barge-in) — in the pause between two
 * sentences too — and then goes out as the next message; an answer that landed before the mode
 * came on is never read; switching off releases the mic and silences the voice; a window that
 * cannot read aloud says so and keeps listening; and a mic that cannot be opened says that
 * instead of pretending.
 */

vi.mock("@/lib/api", () => ({
  getDictationSupport: vi.fn(async () => ({ support: "yes", how: "local" })),
  transcribe: vi.fn(),
  warmTranscriber: vi.fn(async () => {}),
}));

const FRAME = DEFAULT_SEGMENTER.frameMs;
const HANGOVER = Math.ceil(DEFAULT_SEGMENTER.hangoverMs / FRAME);

class FakeMic implements MicrophoneLike {
  readonly sampleRate = 16_000;
  sink: FrameSink | null = null;
  started = 0;
  stopped = 0;
  fail = false;
  async start(sink: FrameSink): Promise<void> {
    if (this.fail) throw new Error("no mic");
    this.sink = sink;
    this.started += 1;
  }
  stop(): void {
    this.sink = null;
    this.stopped += 1;
  }
  /** Feed `n` frames at `level` (each sample at that level, so RMS == level). */
  hear(level: number, n: number): void {
    for (let i = 0; i < n; i++) this.sink?.(new Float32Array(1024).fill(level));
  }
}

/** A voice with a queue, like the browser's: pieces are read in order, `finish` ends the one
 *  being read, `cancel` drops them all. */
class FakeSpeaker implements SpeakerLike {
  spoken: string[] = [];
  cancels = 0;
  present = true;
  private queue: Array<() => void> = [];
  private idlers: Array<() => void> = [];
  available(): boolean {
    return this.present;
  }
  speaking(): boolean {
    return this.queue.length > 0;
  }
  idle(): Promise<void> {
    if (!this.queue.length) return Promise.resolve();
    return new Promise((resolve) => this.idlers.push(resolve));
  }
  speak(text: string): Promise<void> {
    this.spoken.push(text);
    return new Promise((resolve) => {
      this.queue.push(() => {
        resolve();
        if (!this.queue.length) {
          const idlers = this.idlers;
          this.idlers = [];
          for (const i of idlers) i();
        }
      });
    });
  }
  cancel(): void {
    this.cancels += 1;
    const queue = this.queue;
    this.queue = [];
    for (const settle of queue) settle();
  }
  /** The piece being read reached its end on its own. */
  finish(): void {
    const settle = this.queue.shift();
    settle?.();
  }
  /** Every queued piece reaches its end. */
  finishAll(): void {
    while (this.queue.length) this.finish();
  }
}

function harness(over: Partial<{ transcript: Transcript; micFails: boolean; noVoice: boolean }> = {}) {
  const mic = new FakeMic();
  mic.fail = over.micFails === true;
  const speaker = new FakeSpeaker();
  speaker.present = !over.noVoice;
  const transcribe = vi.fn(async (_audio: Blob, _name: string, _language: string) => over.transcript ?? { text: "fix the login page", note: "" });
  const warm = vi.fn(async () => {});
  const deps: VoiceModeDeps = {
    microphone: () => mic,
    speaker,
    transcribe,
    warm,
    segmenter: () => new Segmenter({ frameMs: FRAME }),
  };
  return { mic, speaker, transcribe, warm, deps };
}

function speakUtterance(mic: FakeMic) {
  act(() => {
    mic.hear(0.003, 10);
    mic.hear(0.2, 12);
    mic.hear(0.003, HANGOVER + 1);
  });
}

describe("hands-free voice", () => {
  beforeEach(() => vi.mocked(getDictationSupport).mockResolvedValue({ support: "yes", how: "local" }));

  it("opens the mic when switched on, warms the transcriber, and says it is listening", async () => {
    const { mic, deps, warm } = harness();
    const user = userEvent.setup();
    renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
    expect(warm).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/listening/i);
    expect(screen.getByTestId("voice-mode")).toHaveAttribute("aria-pressed", "true");
  });

  it("transcribes an utterance, hands it over as a message, and says how long it took", async () => {
    const { mic, deps, transcribe } = harness();
    const onUtterance = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<VoiceMode onUtterance={onUtterance} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));

    speakUtterance(mic);

    await waitFor(() => expect(onUtterance).toHaveBeenCalledWith("fix the login page"));
    expect(transcribe).toHaveBeenCalledTimes(1);
    const [audio, name, language] = transcribe.mock.calls[0];
    expect(name).toBe("speech.wav");
    expect(language).toBe("en");
    expect(audio.type).toBe("audio/wav");
    expect(audio.size).toBeGreaterThan(44);
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent(/heard in \d+(\.\d+)? s: fix the login page/i));
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/listening/i);
  });

  it("reads an answer that lands while the mode is on, without its Markdown", async () => {
    const { mic, speaker, deps } = harness();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));

    const answer: SpokenAnswer = { seq: 0, text: "**Done.** See `a.py`.\n\n```py\nx = 1\n```", done: true };
    rerender(<VoiceMode onUtterance={vi.fn()} answer={answer} deps={deps} />);

    await waitFor(() => expect(speaker.spoken).toHaveLength(1));
    expect(speaker.spoken[0]).toContain("Done. See a.py");
    expect(speaker.spoken[0]).not.toContain("x = 1");
    expect(speaker.spoken[0]).not.toContain("**");
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/reading the answer aloud/i);

    act(() => speaker.finish());
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent(/listening/i));
  });

  it("reads the answer as it streams: the first sentence is queued before the turn is done", async () => {
    const { mic, speaker, deps } = harness();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));

    const grow = (text: string, done = false) =>
      rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text, done }} deps={deps} />);
    grow("The login");
    grow("The login is fixed");
    expect(speaker.spoken).toEqual([]); // no sentence is complete yet
    grow("The login is fixed. The logout");
    await waitFor(() => expect(speaker.spoken).toEqual(["The login is fixed."]));
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/reading the answer aloud/i);
    grow("The login is fixed. The logout is next.\n\n- one\n- two", true);
    await waitFor(() => expect(speaker.spoken).toHaveLength(2));
    expect(speaker.spoken[1]).toBe("The logout is next. one two");

    // Between two sentences the mode is still "reading": the voice paused, the answer did not end.
    act(() => speaker.finish());
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/reading the answer aloud/i);
    act(() => speaker.finish());
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent(/listening/i));
  });

  it("stops at the model's own line and says the rest is on the screen", async () => {
    const { mic, speaker, deps } = harness();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));

    const text = "Three files change, all in the API.\n\n---\n\n1. `a.py`\n2. `b.py`\n3. `c.py`";
    rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text, done: false }} deps={deps} />);
    await waitFor(() => expect(speaker.spoken).toHaveLength(2));
    expect(speaker.spoken[0]).toBe("Three files change, all in the API.");
    expect(speaker.spoken[1]).toMatch(/rest is on the screen/i);
    // More of the screen part arriving changes nothing for the voice.
    rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text: text + "\n4. `d.py`", done: true }} deps={deps} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(speaker.spoken).toHaveLength(2);
    expect(speaker.spoken.join(" ")).not.toContain("a.py");
  });

  it("stops after the sentence cap, as a net under a model that ignores the instruction", async () => {
    const { mic, speaker, deps } = harness();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));

    const sentences = Array.from({ length: MAX_SPOKEN_SENTENCES + 4 }, (_, i) => `Sentence number ${i + 1}.`);
    rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text: sentences.join(" "), done: true }} deps={deps} />);
    await waitFor(() => expect(speaker.spoken.length).toBeGreaterThanOrEqual(2));
    const read = speaker.spoken.filter((p) => !/rest is on the screen/i.test(p)).join(" ");
    expect(read).toContain(`Sentence number ${MAX_SPOKEN_SENTENCES}.`);
    expect(read).not.toContain(`Sentence number ${MAX_SPOKEN_SENTENCES + 4}.`);
    expect(speaker.spoken[speaker.spoken.length - 1]).toMatch(/rest is on the screen/i);
  });

  it("stops reading when the person speaks over it, and sends what they said", async () => {
    const { mic, speaker, deps } = harness({ transcript: { text: "stop, do the logout instead", note: "" } });
    const onUtterance = vi.fn();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={onUtterance} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
    rerender(<VoiceMode onUtterance={onUtterance} answer={{ seq: 0, text: "A long answer.", done: true }} deps={deps} />);
    await waitFor(() => expect(speaker.speaking()).toBe(true));

    // The agent's voice through the speakers, then a person louder than it.
    act(() => mic.hear(0.05, 40));
    expect(speaker.cancels).toBe(0);
    act(() => {
      mic.hear(0.4, 12);
      mic.hear(0.003, HANGOVER + 1);
    });

    expect(speaker.cancels).toBe(1);
    expect(speaker.speaking()).toBe(false);
    await waitFor(() => expect(onUtterance).toHaveBeenCalledWith("stop, do the logout instead"));
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent(/listening/i));
  });

  it("treats speech in the pause between two sentences as an interruption, and reads no more of that answer", async () => {
    const { mic, speaker, deps } = harness({ transcript: { text: "wait", note: "" } });
    const onUtterance = vi.fn();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={onUtterance} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
    rerender(<VoiceMode onUtterance={onUtterance} answer={{ seq: 0, text: "First sentence. Sec", done: false }} deps={deps} />);
    await waitFor(() => expect(speaker.spoken).toEqual(["First sentence."]));
    // The first sentence ends; the second is not complete yet — the voice is silent, the answer is not over.
    act(() => mic.hear(0.05, 40));
    act(() => speaker.finish());
    expect(speaker.speaking()).toBe(false);
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/reading the answer aloud/i);

    act(() => {
      mic.hear(0.4, 12);
      mic.hear(0.003, HANGOVER + 1);
    });
    await waitFor(() => expect(onUtterance).toHaveBeenCalledWith("wait"));
    rerender(<VoiceMode onUtterance={onUtterance} answer={{ seq: 0, text: "First sentence. Second sentence. Third.", done: true }} deps={deps} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(speaker.spoken).toEqual(["First sentence."]);
  });

  it("never reads an answer that landed before the mode came on", async () => {
    const { mic, speaker, deps } = harness();
    const user = userEvent.setup();
    renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 3, text: "old answer", done: true }} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
    await new Promise((r) => setTimeout(r, 20));
    expect(speaker.spoken).toEqual([]);
  });

  it("releases the mic and silences the voice when switched off", async () => {
    const { mic, speaker, deps } = harness();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
    rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text: "reading this", done: true }} deps={deps} />);
    await waitFor(() => expect(speaker.speaking()).toBe(true));

    await user.click(screen.getByTestId("voice-mode"));

    expect(mic.stopped).toBeGreaterThanOrEqual(1);
    expect(speaker.cancels).toBeGreaterThanOrEqual(1);
    expect(screen.getByTestId("voice-mode")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("voice-status")).not.toBeInTheDocument();
  });

  it("says when the window cannot read aloud, and keeps listening", async () => {
    const { mic, speaker, deps } = harness({ noVoice: true });
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
    expect(screen.getByTestId("voice-note")).toHaveTextContent(/cannot read aloud/i);
    rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text: "silent answer", done: true }} deps={deps} />);
    await new Promise((r) => setTimeout(r, 20));
    expect(speaker.spoken).toEqual([]);
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/listening/i);
  });

  it("says there is no microphone instead of pretending to listen", async () => {
    const { deps } = harness({ micFails: true });
    const user = userEvent.setup();
    renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(screen.getByTestId("voice-note")).toHaveTextContent(/no microphone/i));
    expect(screen.getByTestId("voice-mode")).toHaveAttribute("aria-pressed", "false");
  });

  it("is disabled where nothing can transcribe, with the same reason dictation gives", async () => {
    vi.mocked(getDictationSupport).mockResolvedValue({ support: "no", how: "" });
    const { deps } = harness();
    renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await waitFor(() => expect(screen.getByTestId("voice-mode")).toBeDisabled());
  });
});
