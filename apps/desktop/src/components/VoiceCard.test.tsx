import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { VoiceCard } from "@/components/VoiceCard";
import { VOICE_KEY, VOICE_LANG_KEY, VOICE_RATE_KEY } from "@/lib/voice/speaker";
import { renderWithProviders } from "@/test/utils";

/**
 * The Settings card for the voice that reads. Driven with the voice list Edge on Windows returned
 * on 2026-09-17 (trimmed): the list the person sees is the app's language only, neural voices
 * first; a choice is kept in this window; Listen reads a sentence with the chosen voice.
 */

function voice(name: string, lang: string, localService: boolean): SpeechSynthesisVoice {
  return { name, lang, localService, default: false, voiceURI: name } as SpeechSynthesisVoice;
}

const EDGE = [
  voice("Microsoft Daniel - Portuguese (Brazil)", "pt-BR", true),
  voice("Microsoft Maria - Portuguese (Brazil)", "pt-BR", true),
  voice("Microsoft Ava Online (Natural) - English (United States)", "en-US", false),
  voice("Microsoft Francisca Online (Natural) - Portuguese (Brazil)", "pt-BR", false),
  voice("Microsoft Antônio Online (Natural) - Portuguese (Brazil)", "pt-BR", false),
];

class FakeSynthesis {
  spoken: SpeechSynthesisUtterance[] = [];
  list: SpeechSynthesisVoice[] = EDGE;
  readonly speaking = false;
  getVoices() {
    return this.list;
  }
  addEventListener() {}
  removeEventListener() {}
  speak(u: SpeechSynthesisUtterance) {
    this.spoken.push(u);
  }
  cancel() {}
}

class FakeUtterance {
  lang = "";
  rate = 1;
  voice: SpeechSynthesisVoice | null = null;
  onend: unknown = null;
  onerror: unknown = null;
  constructor(public text: string) {}
}

describe("the voice card", () => {
  let synth: FakeSynthesis;
  beforeEach(() => {
    synth = new FakeSynthesis();
    vi.stubGlobal("speechSynthesis", synth);
    vi.stubGlobal("SpeechSynthesisUtterance", FakeUtterance);
    localStorage.clear();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists the voices of the app's language with the neural ones first, automatic on top", () => {
    localStorage.setItem("chimera.lang", "pt");
    renderWithProviders(<VoiceCard />);
    // Scoped to the voice list: the card has two more selects now (pace and language).
    const options = within(screen.getByTestId("voice-select")).getAllByRole("option").map((o) => o.textContent);
    expect(options[0]).toMatch(/autom/i);
    expect(options[1]).toContain("Francisca Online (Natural)");
    expect(options[2]).toContain("Antônio Online (Natural)");
    expect(options.slice(3)).toEqual(["Microsoft Daniel - Portuguese (Brazil)", "Microsoft Maria - Portuguese (Brazil)"]);
    expect(options.join(" ")).not.toContain("Ava");
  });

  it("keeps the choice in this window, and Listen reads a sentence with it", async () => {
    localStorage.setItem("chimera.lang", "pt");
    renderWithProviders(<VoiceCard />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("voice-select"), "Microsoft Antônio Online (Natural) - Portuguese (Brazil)");
    expect(localStorage.getItem(VOICE_KEY)).toBe("Microsoft Antônio Online (Natural) - Portuguese (Brazil)");

    await user.click(screen.getByTestId("voice-listen"));
    await waitFor(() => expect(synth.spoken).toHaveLength(1));
    expect(synth.spoken[0].voice?.name).toContain("Antônio");
    expect(synth.spoken[0].lang).toBe("pt-BR");
    expect(synth.spoken[0].text).toMatch(/ler as suas respostas/i);

    await user.selectOptions(screen.getByTestId("voice-select"), "");
    expect(localStorage.getItem(VOICE_KEY)).toBeNull();
  });

  it("shows automatic when the chosen voice is no longer on this machine", () => {
    localStorage.setItem(VOICE_KEY, "Microsoft Thalita multilíngue Online (Natural) - Portuguese (Brazil)");
    renderWithProviders(<VoiceCard />);
    expect(screen.getByTestId("voice-select")).toHaveValue("");
  });

  it("reads at the normal pace and in the interface's language until something else is chosen", () => {
    localStorage.setItem("chimera.lang", "pt");
    renderWithProviders(<VoiceCard />);
    expect(screen.getByTestId("voice-rate")).toHaveValue("1");
    expect(screen.getByTestId("voice-lang")).toHaveValue("");
    // The four steps, in the interface's own way of writing a decimal.
    const rates = Array.from(screen.getByTestId("voice-rate").querySelectorAll("option")).map((o) => o.textContent);
    expect(rates).toEqual(["0,8×", "1× — normal", "1,25×", "1,5×"]);
  });

  it("keeps a chosen pace in this window, and Listen reads at it", async () => {
    renderWithProviders(<VoiceCard />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("voice-rate"), "1.25");
    expect(localStorage.getItem(VOICE_RATE_KEY)).toBe("1.25");

    await user.click(screen.getByTestId("voice-listen"));
    await waitFor(() => expect(synth.spoken).toHaveLength(1));
    expect(synth.spoken[0].rate).toBe(1.25);
  });

  it("lists the voices of a chosen voice language, and Listen reads that language's sentence", async () => {
    // The app in Portuguese, the voice in English.
    localStorage.setItem("chimera.lang", "pt");
    renderWithProviders(<VoiceCard />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByTestId("voice-lang"), "en");
    expect(localStorage.getItem(VOICE_LANG_KEY)).toBe("en");

    const options = within(screen.getByTestId("voice-select")).getAllByRole("option");
    expect(options.map((o) => o.textContent).slice(1)).toEqual(["Microsoft Ava Online (Natural) - English (United States)"]);

    await user.click(screen.getByTestId("voice-listen"));
    await waitFor(() => expect(synth.spoken).toHaveLength(1));
    expect(synth.spoken[0].lang).toBe("en-US");
    expect(synth.spoken[0].voice?.name).toContain("Ava");
    expect(synth.spoken[0].text).toMatch(/read your answers/i);

    // Back to the interface's: stored as nothing, so it keeps following the interface.
    await user.selectOptions(screen.getByTestId("voice-lang"), "");
    expect(localStorage.getItem(VOICE_LANG_KEY)).toBeNull();
  });

  it("says when the window has no voice for a chosen voice language", async () => {
    localStorage.setItem(VOICE_LANG_KEY, "ja");
    renderWithProviders(<VoiceCard />);
    expect(screen.getByTestId("voice-lang")).toHaveValue("ja");
    expect(screen.getByTestId("voice-none")).toHaveTextContent(/chosen language/i);
  });

  it("says when the window has no voice at all", () => {
    synth.list = [];
    renderWithProviders(<VoiceCard />);
    expect(screen.getByTestId("voice-none")).toBeInTheDocument();
    expect(screen.getByTestId("voice-listen")).toBeDisabled();
  });
});
