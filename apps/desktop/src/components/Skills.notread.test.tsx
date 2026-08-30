/** A count of zero means two different things, and the screen was showing the count without the flag.
 *
 * Measured on a real install: fourteen learned cards, every one marked ACTIVE, every one reading
 * `0 uses · 0 wins`. The available reading is that the agent tried them and they were useless. What
 * actually happened is that nothing consulted them — retrieval is off by default because it was
 * measured (+16.7pp, a confidence interval including zero, +300% tokens) and left off.
 *
 * The person who draws the wrong conclusion goes looking for a bug in the cards.
 */

import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Skills } from "@/components/Skills";
import { getSkills } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

// The FULL surface the screen touches, not a plausible subset: a mock missing one export throws
// during render, and every assertion in the file then fails about a function none of them test.
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
}));

const mockSkills = vi.mocked(getSkills);

function body(cards_read: boolean) {
  return {
    stats: [{
      name: "build_standalone_html_from_brief", kind: "learned", status: "active",
      provenance: "clean", uses: 0, successes: 0, rate: null,
    }],
    retirement_candidates: [],
    cards_read,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Skills — why every count is zero", () => {
  it("says nothing reads these when retrieval is off", async () => {
    mockSkills.mockResolvedValue(body(false));

    renderWithProviders(<Skills />);

    expect(await screen.findByText(/nothing consults them/i)).toBeTruthy();
  });

  it("stays quiet when retrieval IS on, because then zero is a verdict", async () => {
    // The note must not become furniture. With reading on, `0 uses` really does mean the card was
    // available and never picked — and a permanent disclaimer would explain that away.
    mockSkills.mockResolvedValue(body(true));

    renderWithProviders(<Skills />);

    await screen.findByText(/build_standalone_html_from_brief/);
    expect(screen.queryByText(/nothing consults them/i)).toBeNull();
  });

  it("still lists the cards themselves", async () => {
    // The note is an explanation, not a replacement: it sits above the list and the list stays.
    mockSkills.mockResolvedValue(body(false));

    renderWithProviders(<Skills />);

    expect(await screen.findByText(/build_standalone_html_from_brief/)).toBeTruthy();
  });
});
