import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DictateButton } from "@/components/code/Attachments";
import { getDictationSupport, transcribe } from "@/lib/api";
import { VOICE_LANG_KEY } from "@/lib/voice/speaker";
import { renderWithProviders } from "@/test/utils";

/**
 * Dictation by holding a chord: the same start and stop as the Dictate button, so the two ways in
 * cannot drift apart. Pinned: holding the chord opens the microphone and records, letting go stops
 * and transcribes into the draft; the language hint is the voice's language from Settings, the
 * interface's when none was chosen; a tap that ends before the microphone opens gives it straight
 * back instead of recording a silence nobody will stop; a recording started with the button is not
 * stopped by the release of a hold that started nothing; a click while a held chord is still asking
 * for the microphone does not ask again; and nothing is opened while dictation is unavailable, or
 * while the composer is mounted but not on screen.
 */

vi.mock("@/lib/api", () => ({
  getDictationSupport: vi.fn(async () => ({ support: "yes", how: "local" })),
  getVisionSupport: vi.fn(),
  transcribe: vi.fn(async () => ({ text: "hello there", note: "" })),
  uploadAttachment: vi.fn(),
  warmTranscriber: vi.fn(async () => {}),
}));

class FakeRecorder {
  static made: FakeRecorder[] = [];
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  state = "inactive";
  constructor(public stream: MediaStream) {
    FakeRecorder.made.push(this);
  }
  start() {
    this.state = "recording";
  }
  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["x"], { type: "audio/webm" }) });
    this.onstop?.();
  }
}

function fakeStream() {
  const track = { stop: vi.fn() };
  return { track, stream: { getTracks: () => [track] } as unknown as MediaStream };
}

function chord(type: "keydown" | "keyup", code = "Space", key = " ") {
  act(() => {
    window.dispatchEvent(new KeyboardEvent(type, { code, key, ctrlKey: true, shiftKey: true, cancelable: true }));
  });
}

describe("dictation by holding the chord", () => {
  let getUserMedia: ReturnType<typeof vi.fn>;
  let mic: ReturnType<typeof fakeStream>;

  beforeEach(() => {
    localStorage.clear();
    FakeRecorder.made = [];
    mic = fakeStream();
    getUserMedia = vi.fn(async () => mic.stream);
    vi.stubGlobal("MediaRecorder", FakeRecorder);
    Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia }, configurable: true });
    vi.mocked(transcribe).mockClear();
    vi.mocked(getDictationSupport).mockResolvedValue({ support: "yes", how: "local" });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("records while held and types what was said when let go, like the button", async () => {
    const onText = vi.fn();
    renderWithProviders(<DictateButton onText={onText} />);
    await waitFor(() => expect(getDictationSupport).toHaveBeenCalled());

    chord("keydown");
    await waitFor(() => expect(FakeRecorder.made).toHaveLength(1));
    expect(getUserMedia).toHaveBeenCalledWith({ audio: true });
    expect(FakeRecorder.made[0].state).toBe("recording");
    await waitFor(() => expect(screen.getByRole("button", { pressed: true })).toBeInTheDocument());

    chord("keyup");
    await waitFor(() => expect(onText).toHaveBeenCalledWith("hello there"));
    expect(mic.track.stop).toHaveBeenCalled();
    // The interface's language is the hint until a voice language is chosen.
    expect(vi.mocked(transcribe).mock.calls[0][2]).toBe("en");
  });

  it("sends the voice language from Settings as the transcriber's hint", async () => {
    localStorage.setItem(VOICE_LANG_KEY, "pt");
    const onText = vi.fn();
    renderWithProviders(<DictateButton onText={onText} />);
    await waitFor(() => expect(getDictationSupport).toHaveBeenCalled());

    chord("keydown");
    await waitFor(() => expect(FakeRecorder.made).toHaveLength(1));
    chord("keyup");
    await waitFor(() => expect(onText).toHaveBeenCalled());
    expect(vi.mocked(transcribe).mock.calls[0][2]).toBe("pt");
  });

  it("gives the microphone straight back when the key is up before it opened", async () => {
    let grant: (s: MediaStream) => void = () => {};
    getUserMedia.mockImplementation(() => new Promise<MediaStream>((resolve) => (grant = resolve)));
    renderWithProviders(<DictateButton onText={vi.fn()} />);
    await waitFor(() => expect(getDictationSupport).toHaveBeenCalled());

    chord("keydown");
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());
    chord("keyup");
    await act(async () => grant(mic.stream));

    expect(mic.track.stop).toHaveBeenCalled();
    expect(FakeRecorder.made).toHaveLength(0);
    expect(screen.getByRole("button", { pressed: false })).toBeInTheDocument();
  });

  it("does not let a hold that started nothing stop a recording started with the button", async () => {
    const user = userEvent.setup();
    renderWithProviders(<DictateButton onText={vi.fn()} />);
    await waitFor(() => expect(getDictationSupport).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: /dictate/i }));
    await waitFor(() => expect(FakeRecorder.made).toHaveLength(1));
    // Already recording, so this press starts nothing — and its release must stop nothing.
    chord("keydown");
    chord("keyup", "ControlLeft", "Control");
    expect(FakeRecorder.made).toHaveLength(1);
    expect(FakeRecorder.made[0].state).toBe("recording");
  });

  it("opens nothing while the composer is mounted but hidden, as under a maximised viewer", async () => {
    // The Code screen keeps the conversation mounted under `hidden` (display: none) while the
    // viewer is maximised, or a file is open on a narrow window. A hold there would record into
    // a draft nobody can see, with every sign of it hidden too.
    renderWithProviders(
      <div style={{ display: "none" }}>
        <DictateButton onText={vi.fn()} />
      </div>,
    );
    await waitFor(() => expect(getDictationSupport).toHaveBeenCalled());
    await act(async () => {});

    chord("keydown");
    chord("keyup");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("asks the engine whether the button is visible where the engine can say", async () => {
    // The WebView2 has `checkVisibility`; jsdom does not, so the walk above is what it exercises.
    const check = vi.fn(() => false);
    Object.defineProperty(HTMLElement.prototype, "checkVisibility", { value: check, configurable: true });
    try {
      renderWithProviders(<DictateButton onText={vi.fn()} />);
      await waitFor(() => expect(getDictationSupport).toHaveBeenCalled());
      await act(async () => {});
      chord("keydown");
      chord("keyup");
      expect(check).toHaveBeenCalled();
      expect(getUserMedia).not.toHaveBeenCalled();
    } finally {
      delete (HTMLElement.prototype as { checkVisibility?: unknown }).checkVisibility;
    }
  });

  it("does not ask twice when the button is clicked while a held chord is still asking", async () => {
    let grant: (s: MediaStream) => void = () => {};
    getUserMedia.mockImplementation(() => new Promise<MediaStream>((resolve) => (grant = resolve)));
    const user = userEvent.setup();
    const onText = vi.fn();
    renderWithProviders(<DictateButton onText={onText} />);
    await waitFor(() => expect(getDictationSupport).toHaveBeenCalled());

    chord("keydown");
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledOnce());
    // Still idle on screen, so the click reaches `start` — which must not ask again.
    await user.click(screen.getByRole("button", { name: /dictate/i }));
    expect(getUserMedia).toHaveBeenCalledOnce();

    await act(async () => grant(mic.stream));
    expect(FakeRecorder.made).toHaveLength(1);
    chord("keyup");
    await waitFor(() => expect(onText).toHaveBeenCalledWith("hello there"));
    expect(FakeRecorder.made[0].state).toBe("inactive");
    expect(mic.track.stop).toHaveBeenCalled();
  });

  it("opens nothing while dictation is unavailable", async () => {
    vi.mocked(getDictationSupport).mockResolvedValue({ support: "no", how: "" });
    renderWithProviders(<DictateButton onText={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /dictate/i })).toBeDisabled());

    chord("keydown");
    chord("keyup");
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("names the chord on the button, for the keyboard and for assistive technology", async () => {
    renderWithProviders(<DictateButton onText={vi.fn()} />);
    const button = await screen.findByRole("button", { name: /dictate/i });
    expect(button).toHaveAttribute("aria-keyshortcuts", "Control+Shift+Space Meta+Shift+Space");
    await waitFor(() => expect(button.getAttribute("title")).toMatch(/Ctrl\+Shift\+Space/));
  });
});
