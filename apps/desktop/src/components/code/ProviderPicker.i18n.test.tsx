import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ProviderPicker } from "@/components/code/ProviderPicker";
import { TooltipProvider } from "@/components/ui/tooltip";
import { getDoctor } from "@/lib/api";
import { DICTS } from "@/lib/i18n";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({ getDoctor: vi.fn() }));

/**
 * A tooltip is one sentence. It cannot be half in the reader's language.
 *
 * It was: the prefix came from the dictionary and the rest came from the server, so a Portuguese
 * install hint read "Não instalado aqui — npm i -g @google/gemini-cli (the ACP mode is flagged
 * experimental upstream)". The command was right to leave alone; the prose beside it was not, and
 * one string held both. For an agent that IS installed it was worse — `notes` was rendered raw, so
 * that tooltip was English end to end, which is the case a working install shows every day.
 */

function doctor(agents: unknown[]) {
  return {
    has_any_key: true,
    configured_providers: ["openrouter"],
    default_model: "m",
    tiers: { weak: "w", mid: "m", top: "t" },
    memory_backend: "json",
    cache: false,
    sandbox: "local",
    external_agents: agents,
  };
}

const GEMINI_MISSING = {
  key: "gemini",
  label: "Gemini CLI",
  available: false,
  command: "gemini --experimental-acp",
  install_hint: "npm i -g @google/gemini-cli",
  writes_directly: true,
  notes: "ACP mode is experimental upstream and its behaviour may change between releases.",
};

const CLAUDE_PRESENT = {
  key: "claude",
  label: "Claude Code",
  available: true,
  command: "npx -y @agentclientprotocol/claude-agent-acp",
  install_hint: "npm i -g @agentclientprotocol/claude-agent-acp",
  writes_directly: true,
  notes: "Needs Node 22+ and npx on PATH. Uses the Claude Agent SDK.",
};

function picker() {
  return renderWithProviders(
    <TooltipProvider>
      <ProviderPicker value="" onChange={() => {}} />
    </TooltipProvider>,
  );
}

/** The tooltip a keyboard user hears: focus the control, then read what describes it.
 *
 * Reached through `aria-describedby` rather than by looking for the text anywhere on screen. That
 * is the wiring the whole rc13 fix was about — an explanation the assistive tree cannot reach is
 * not an explanation — so a test that found the text by any other route would pass over a tooltip
 * nobody is told about.
 */
async function describedBy(label: string): Promise<string> {
  const button = await screen.findByRole("button", { name: label });
  button.focus();
  return await waitFor(() => {
    const id = button.getAttribute("aria-describedby");
    const text = (id && document.getElementById(id)?.textContent) || "";
    if (!text) throw new Error(`no tooltip described "${label}" yet`);
    return text;
  });
}

describe("ProviderPicker — the tooltip is in one language", () => {
  beforeEach(() => {
    localStorage.setItem("chimera.lang", "pt");
    vi.mocked(getDoctor).mockReset();
  });

  it("keeps the command and translates everything around it", async () => {
    vi.mocked(getDoctor).mockResolvedValue(doctor([CLAUDE_PRESENT, GEMINI_MISSING]) as never);
    picker();

    const tip = await describedBy("Gemini CLI");

    expect(tip).toContain("Não instalado aqui");
    expect(tip).toContain("npm i -g @google/gemini-cli"); // a command is not translated
    expect(tip).not.toContain("flagged experimental upstream"); // the prose that rode along
  });

  it("translates the note of an agent that IS installed", async () => {
    vi.mocked(getDoctor).mockResolvedValue(doctor([CLAUDE_PRESENT]) as never);
    picker();

    const tip = await describedBy("Claude Code");

    expect(tip).toContain("ferramentas de arquivo e shell");
    expect(tip).not.toContain("Uses the Claude Agent SDK");
  });

  it("shows the server's own words for a provider the dictionary cannot know", async () => {
    // A `custom` adapter someone registered has no key here and never will. Falling through to the
    // raw identifier — which is what `t` returns for a missing key — would put
    // "code.provider.note.my-adapter" in front of a user.
    const mine = { ...CLAUDE_PRESENT, key: "my-adapter", label: "My adapter", notes: "Mine." };
    vi.mocked(getDoctor).mockResolvedValue(doctor([mine]) as never);
    picker();

    const tip = await describedBy("My adapter");

    expect(tip).toBe("Mine.");
    expect(tip).not.toContain("code.provider.note.");
  });

  it("carries the three notes in every language the app offers", () => {
    // The dictionaries are the deliverable here: a key present in `en` and missing everywhere else
    // renders in English through `t`'s own fallback, which is exactly the defect being fixed and
    // would leave every assertion above still passing.
    for (const [lang, dict] of Object.entries(DICTS)) {
      const table = dict as Record<string, string>;
      for (const key of ["claude", "gemini", "custom"]) {
        expect(table[`code.provider.note.${key}`], `${lang} lacks note.${key}`).toBeTruthy();
      }
      expect(table["code.provider.missing"], `${lang} lost the hint slot`).toContain("{hint}");
    }
  });
});
