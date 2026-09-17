import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceMode, type SpokenAnswer, type VoiceModeDeps } from "@/components/code/VoiceMode";
import { getDictationSupport, type Transcript } from "@/lib/api";
import { DEFAULT_SEGMENTER, Segmenter } from "@/lib/voice/segmenter";
import type { FrameSink, MicrophoneLike } from "@/lib/voice/microphone";
import type { SpeakerLike } from "@/lib/voice/speaker";
import { renderWithProviders } from "@/test/utils";

/**
 * The hands-free state machine, driven with a fake microphone and a fake voice.
 *
 * What is pinned: switching the mode on opens the mic and says "listening"; an utterance is
 * transcribed and handed to the caller as a message, with how long the transcription took; an
 * answer that lands while the mode is on is read aloud, stripped of its Markdown; speech over the
 * reading cancels it (barge-in) and then goes out as the next message; an answer that landed
 * before the mode came on is never read; switching off releases the mic and silences the voice;
 * a window that cannot read aloud says so and keeps listening; and a mic that cannot be opened
 * says that instead of pretending.
 */

vi.mock("@/lib/api", () => ({
  getDictationSupport: vi.fn(async () => ({ support: "yes", how: "local" })),
  transcribe: vi.fn(),
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

class FakeSpeaker implements SpeakerLike {
  spoken: string[] = [];
  cancels = 0;
  present = true;
  private resolveCurrent: (() => void) | null = null;
  available(): boolean {
    return this.present;
  }
  speaking(): boolean {
    return this.resolveCurrent !== null;
  }
  speak(text: string): Promise<void> {
    this.spoken.push(text);
    return new Promise((resolve) => {
      this.resolveCurrent = () => {
        this.resolveCurrent = null;
        resolve();
      };
    });
  }
  cancel(): void {
    this.cancels += 1;
    this.resolveCurrent?.();
  }
  /** The reading reached its end on its own. */
  finish(): void {
    this.resolveCurrent?.();
  }
}

function harness(over: Partial<{ transcript: Transcript; micFails: boolean; noVoice: boolean }> = {}) {
  const mic = new FakeMic();
  mic.fail = over.micFails === true;
  const speaker = new FakeSpeaker();
  speaker.present = !over.noVoice;
  const transcribe = vi.fn(async (_audio: Blob, _name: string) => over.transcript ?? { text: "fix the login page", note: "" });
  const deps: VoiceModeDeps = {
    microphone: () => mic,
    speaker,
    transcribe,
    segmenter: () => new Segmenter({ frameMs: FRAME }),
  };
  return { mic, speaker, transcribe, deps };
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

  it("opens the mic when switched on, and says it is listening", async () => {
    const { mic, deps } = harness();
    const user = userEvent.setup();
    renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
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
    const [audio, name] = transcribe.mock.calls[0];
    expect(name).toBe("speech.wav");
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

    const answer: SpokenAnswer = { seq: 0, text: "**Done.** See `a.py`.\n\n```py\nx = 1\n```" };
    rerender(<VoiceMode onUtterance={vi.fn()} answer={answer} deps={deps} />);

    await waitFor(() => expect(speaker.spoken).toHaveLength(1));
    expect(speaker.spoken[0]).toContain("Done. See a.py");
    expect(speaker.spoken[0]).not.toContain("x = 1");
    expect(speaker.spoken[0]).not.toContain("**");
    expect(screen.getByTestId("voice-status")).toHaveTextContent(/reading the answer aloud/i);

    act(() => speaker.finish());
    await waitFor(() => expect(screen.getByTestId("voice-status")).toHaveTextContent(/listening/i));
  });

  it("stops reading when the person speaks over it, and sends what they said", async () => {
    const { mic, speaker, deps } = harness({ transcript: { text: "stop, do the logout instead", note: "" } });
    const onUtterance = vi.fn();
    const user = userEvent.setup();
    const { rerender } = renderWithProviders(<VoiceMode onUtterance={onUtterance} answer={null} deps={deps} />);
    await user.click(screen.getByTestId("voice-mode"));
    await waitFor(() => expect(mic.started).toBe(1));
    rerender(<VoiceMode onUtterance={onUtterance} answer={{ seq: 0, text: "A long answer." }} deps={deps} />);
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

  it("never reads an answer that landed before the mode came on", async () => {
    const { mic, speaker, deps } = harness();
    const user = userEvent.setup();
    renderWithProviders(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 3, text: "old answer" }} deps={deps} />);
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
    rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text: "reading this" }} deps={deps} />);
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
    rerender(<VoiceMode onUtterance={vi.fn()} answer={{ seq: 0, text: "silent answer" }} deps={deps} />);
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
