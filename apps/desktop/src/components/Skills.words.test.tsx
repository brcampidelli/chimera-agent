import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Skills } from "@/components/Skills";
import { getSkillBundles, getSkillCatalog, getSkillLibrary, getSkills } from "@/lib/api";
import { legacyRedirect } from "@/lib/legacyRoutes";
import { formatHash, parseHash } from "@/lib/router";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSkills: vi.fn(),
  approveSkill: vi.fn(),
  retireSkill: vi.fn(),
  getSkillLibrary: vi.fn(async () => []),
  getSkillLibraryCard: vi.fn(),
  importSkillLibraryCard: vi.fn(),
  getSkillCatalog: vi.fn(async () => []),
  getSkillBundles: vi.fn(async () => []),
  installSkillBundle: vi.fn(),
  setSkillBundleStatus: vi.fn(),
  uninstallSkillBundle: vi.fn(),
  checkSkillBundleUpdate: vi.fn(),
  getEffectiveSkills: vi.fn(async () => ({ bundles: [], bundle_text: "", cards_read: false, cards: [], cards_k: 0 })),
  // The upload panel sits on the same screen; nothing here presses its buttons.
  importSkill: vi.fn(),
  getSkillBundleText: vi.fn(),
  getPinnedCatalog: vi.fn(async () => ({ kind: "skills", repo: "brcampidelli/chimera-skills", sha: "b".repeat(40), items: [], available: true, reason: null, problems: [] })),
}));

function stat(over: Record<string, unknown> = {}) {
  return {
    name: "chimera-thin-vertical-slice",
    kind: "pattern",
    status: "active",
    provenance: "clean",
    uses: 0,
    successes: 0,
    rate: null,
    ...over,
  };
}

/**
 * The learned-skills rows printed two English literals in every language ("tainted", "retire?"), the
 * second in the red of a failure for what is a suggestion. And the one section that asks something of
 * the person sat second in a stack of five, with nothing above the fold saying it was waiting.
 */
describe("the skills screen in the reader's words", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "#/settings?tab=skills");
    vi.mocked(getSkills).mockReset();
  });

  it("translates the provenance and the retirement hint instead of printing them raw", async () => {
    vi.mocked(getSkills).mockResolvedValue({
      stats: [stat({ provenance: "tainted" })],
      retirement_candidates: ["chimera-thin-vertical-slice"],
      cards_read: true,
    } as never);
    renderWithProviders(<Skills />);
    expect(await screen.findByText("unverified")).toBeInTheDocument();
    expect(screen.queryByText("tainted")).not.toBeInTheDocument();
    expect(screen.getByText("retire?")).toBeInTheDocument();
  });

  it("says above the fold when a learned skill waits for a decision", async () => {
    const user = userEvent.setup();
    vi.mocked(getSkills).mockResolvedValue({
      stats: [stat({ status: "pending" }), stat({ name: "other", status: "pending" })],
      retirement_candidates: [],
      cards_read: true,
    } as never);
    renderWithProviders(<Skills />);
    expect(await screen.findByText("2 learned skill(s) wait for your decision.")).toBeInTheDocument();
    // "Review" used to scroll a long page to the learned panel. On the catalog shell the cards that
    // wait are their own section, so it opens that section — in the address, with focus on its title.
    await user.click(screen.getByRole("button", { name: "Review" }));
    expect(new URLSearchParams(window.location.hash.split("?")[1]).get("section")).toBe("waiting");
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Waiting for you" })));
  });

  it("lands each of the old page's five section links on that section", async () => {
    // The long page had a row of in-page links to its five sections. The page is a catalog screen
    // now, and the sections live in the address; each old link still has to arrive where it pointed.
    vi.mocked(getSkills).mockResolvedValue({ stats: [stat()], retirement_candidates: [], cards_read: true } as never);
    vi.mocked(getSkillLibrary).mockResolvedValue([
      { name: "verify-before-claiming", description: "Check.", version: "0.1.0", kind: "pattern", stage: "verify",
        topic: "software-dev", triggers: [], license: "Apache-2.0", body: "", imported: false },
    ] as never);
    vi.mocked(getSkillCatalog).mockResolvedValue([
      { name: "maps", description: "Routes.", topic: "productivity", license: "MIT", permissive: true,
        portability: "native", requires: [], note: "", author: "", homepage: "", installed: "" },
    ] as never);
    vi.mocked(getSkillBundles).mockResolvedValue([
      { name: "my-notes", description: "Notes.", status: "pending", license: "", source: "upload: notes.md",
        ref: "", installed_at: "", files: ["SKILL.md"], origin: "upload", provenance: "tainted",
        committed_at: "", reconfirm: false, source_path: "", source_hash: "", collisions: [] },
    ] as never);
    const lands: Record<string, () => Promise<unknown>> = {
      "skills-active": () => screen.findByText("Active now"),
      "skills-learned": () => screen.findByRole("region", { name: "Learned skills" }),
      "skills-library": () => screen.findByRole("region", { name: "From the Chimera library" }),
      "skills-yours": () => screen.findByRole("article", { name: "my-notes" }),
      "skills-catalog": () => screen.findByRole("article", { name: "maps" }),
    };
    expect(Object.keys(lands)).toHaveLength(5);
    for (const [anchor, find] of Object.entries(lands)) {
      const old = parseHash(`#/knowledge?tab=skills#${anchor}`);
      const moved = legacyRedirect(old);
      if (!moved) throw new Error(`${anchor} no longer redirects`);
      window.history.replaceState(null, "", formatHash(moved.view, moved.params));
      const { unmount } = renderWithProviders(<Skills />);
      expect(await find(), anchor).toBeInTheDocument();
      unmount();
    }
  });
});
