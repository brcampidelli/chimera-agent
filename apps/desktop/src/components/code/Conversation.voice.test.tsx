import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { streamCodeTurn, transcribe } from "@/lib/api";
import { DEFAULT_SEGMENTER } from "@/lib/voice/segmenter";
import type { FrameSink } from "@/lib/voice/microphone";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** The window's microphone, replaced by one a test can speak into. */
const mic = vi.hoisted(() => ({ sink: null as FrameSink | null }));
vi.mock("@/lib/voice/microphone", async () => {
  const actual = await vi.importActual<typeof import("@/lib/voice/microphone")>("@/lib/voice/microphone");
  class FakeMicrophone {
    readonly sampleRate = 16_000;
    async start(sink: FrameSink): Promise<void> {
      mic.sink = sink;
    }
    stop(): void {
      mic.sink = null;
    }
  }
  return { ...actual, BrowserMicrophone: FakeMicrophone };
});

/**
 * What a spoken message carries that a typed one does not.
 *
 * A turn that arrived by voice is sent with `spoken: true`, and the server puts the answer-for-
 * the-ear note in that turn's system prompt (pinned in `test_a_spoken_turn_is_answered_for_the_ear`).
 * A typed turn sends exactly what it sent before the field existed: no `spoken` at all. The
 * transcription carries the app's language as the hint.
 */
function mount() {
  renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={false}
      controls={null}
      onOpenFile={() => {}}
    />,
  );
}

function hear(level: number, frames: number) {
  for (let i = 0; i < frames; i++) mic.sink?.(new Float32Array(1024).fill(level));
}

describe("a spoken turn", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    vi.mocked(transcribe).mockReset().mockResolvedValue({ text: "fix the login", note: "" });
    mic.sink = null;
    localStorage.clear();
  });

  it("goes out marked as spoken, with the app's language on its transcription", async () => {
    mount();
    const user = userEvent.setup();
    await user.click(await screen.findByTestId("voice-mode"));
    await waitFor(() => expect(mic.sink).not.toBeNull());

    const hangover = Math.ceil(DEFAULT_SEGMENTER.hangoverMs / DEFAULT_SEGMENTER.frameMs);
    act(() => {
      hear(0.003, 10);
      hear(0.2, 12);
      hear(0.003, hangover + 1);
    });
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).toMatchObject({ message: "fix the login", spoken: true, thinking: false });
    expect(vi.mocked(transcribe).mock.calls[0][2]).toBe("en");
  });

  it("is not what a typed turn sends: no `spoken` field at all, as before the field existed", async () => {
    mount();
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "and the logout{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    const typed = vi.mocked(streamCodeTurn).mock.calls[0][0] as unknown as Record<string, unknown>;
    expect(typed.message).toBe("and the logout");
    expect("spoken" in typed).toBe(false);
    expect("thinking" in typed).toBe(false);
  });
});
