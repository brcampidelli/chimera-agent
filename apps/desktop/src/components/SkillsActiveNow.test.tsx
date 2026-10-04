import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Skills } from "@/components/Skills";
import { getEffectiveSkills, getSkillLibrary, getSkills } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSkills: vi.fn(),
  approveSkill: vi.fn(),
  retireSkill: vi.fn(),
  getSkillLibrary: vi.fn(),
  getSkillLibraryCard: vi.fn(),
  importSkillLibraryCard: vi.fn(),
  getSkillCatalog: vi.fn(async () => []),
  getSkillBundles: vi.fn(async () => []),
  installSkillBundle: vi.fn(),
  setSkillBundleStatus: vi.fn(),
  uninstallSkillBundle: vi.fn(),
  checkSkillBundleUpdate: vi.fn(),
  getEffectiveSkills: vi.fn(),
}));

const TEXT = [
  "Installed skills you may use:",
  '- pdf-forms: Fill PDF forms. (read it with skill_view(name="pdf-forms") before using it)',
].join("\n");

/** Study 29, P7.1: the Skills screen opens with what a run is actually told about skills. */
describe("the Skills screen's 'active now' panel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSkills).mockResolvedValue({ stats: [], retirement_candidates: [], cards_read: false });
    vi.mocked(getSkillLibrary).mockResolvedValue([]);
  });

  it("shows the prompt's own text verbatim, not a rendering of it", async () => {
    const user = userEvent.setup();
    vi.mocked(getEffectiveSkills).mockResolvedValue({
      bundles: [{ name: "pdf-forms", description: "Fill PDF forms.", ref: "c".repeat(40), committed_at: "2026-09-30T08:00:00Z" }],
      bundle_text: TEXT,
      cards_read: false,
      cards: [],
      cards_k: 0,
    });
    renderWithProviders(<Skills />);

    await waitFor(() => expect(screen.getByText("pdf-forms")).toBeInTheDocument());
    expect(screen.getByText(/ccccccc/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /show the exact text/i }));
    // Character for character, newlines included: this is the text the agent reads, and a screen
    // that tidied it would be answering a different question.
    const shown = screen.getByText((_, el) => el?.tagName === "PRE" && el.textContent === TEXT);
    expect(shown).toBeInTheDocument();
  });

  it("names the skills switched on before a switched-on skill reached the prompt", async () => {
    vi.mocked(getEffectiveSkills).mockResolvedValue({
      bundles: [],
      bundle_text: "",
      reconfirm: ["maps", "pdf-forms"],
      cards_read: false,
      cards: [],
      cards_k: 0,
    });
    renderWithProviders(<Skills />);

    const line = await screen.findByText(/switch them on again/i);
    expect(line.textContent).toMatch(/maps, pdf-forms/);
  });

  it("says the list is the one outside any project, and that task-matched built-ins come on top", async () => {
    vi.mocked(getEffectiveSkills).mockResolvedValue({
      bundles: [],
      bundle_text: "",
      cards_read: false,
      cards: [],
      cards_k: 0,
    });
    renderWithProviders(<Skills />);

    await screen.findByText(/outside any project/i);
    expect(screen.getByText(/built-in skills that match its task/i)).toBeInTheDocument();
  });

  it("says plainly that no learned card is read while reading is off", async () => {
    vi.mocked(getEffectiveSkills).mockResolvedValue({
      bundles: [],
      bundle_text: "",
      cards_read: false,
      cards: [],
      cards_k: 0,
    });
    renderWithProviders(<Skills />);

    await waitFor(() => expect(screen.getByText(/learned cards read: no/i)).toBeInTheDocument());
    expect(screen.getByText(/no installed skill is switched on/i)).toBeInTheDocument();
    // No text to show, so no button promising one.
    expect(screen.queryByRole("button", { name: /show the exact text/i })).not.toBeInTheDocument();
  });

  it("with reading on, names the rule and the pool rather than claiming every card is read", async () => {
    vi.mocked(getEffectiveSkills).mockResolvedValue({
      bundles: [],
      bundle_text: "",
      cards_read: true,
      cards: ["a", "b", "c"],
      cards_k: 1,
    });
    renderWithProviders(<Skills />);

    await waitFor(() =>
      expect(screen.getByText(/up to 1 per run, chosen by the task from 3 eligible/i)).toBeInTheDocument(),
    );
  });

  it("shows a library card's triggers on its row, without opening it", async () => {
    vi.mocked(getEffectiveSkills).mockResolvedValue({
      bundles: [],
      bundle_text: "",
      cards_read: false,
      cards: [],
      cards_k: 0,
    });
    vi.mocked(getSkillLibrary).mockResolvedValue([
      {
        name: "verify-before-claiming",
        description: "Run the check.",
        version: "0.1.0",
        kind: "pattern",
        stage: "verify",
        topic: "software-dev",
        triggers: ["about to report success", "saying it works"],
        license: "Apache-2.0",
        body: "",
        imported: false,
      },
    ]);
    renderWithProviders(<Skills />);

    await waitFor(() =>
      expect(screen.getByText(/about to report success · saying it works/)).toBeInTheDocument(),
    );
  });
});
