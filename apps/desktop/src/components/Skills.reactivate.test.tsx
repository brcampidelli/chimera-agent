import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Skills } from "@/components/Skills";
import { approveSkill, getSkills, retireSkill } from "@/lib/api";
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
 * Retiring a skill was a one-way door on this screen.
 *
 * The store calls retirement "proposed-with-review, not deletion" and documents `approve` as the
 * single transition back — but the only button wired to `approve` was gated on `pending`, and the
 * retire button hid itself once the skill was retired. So a retired row had no control beside it at
 * all. Found on a real install: one imported card, retired, 0 uses, 0 wins, and the only way back
 * was editing `skills.json` by hand.
 */
describe("a retired skill can be put back to work", () => {
  beforeEach(() => {
    vi.mocked(getSkills).mockReset();
    vi.mocked(approveSkill).mockReset().mockResolvedValue({ approved: true });
    vi.mocked(retireSkill).mockReset().mockResolvedValue({ retired: true });
  });

  it("offers a way back, and it is not the approve-a-stranger wording", async () => {
    vi.mocked(getSkills).mockResolvedValue({
      stats: [stat({ status: "retired" })],
      retirement_candidates: [], cards_read: true,
    });
    renderWithProviders(<Skills />);

    const botao = await screen.findByRole("button", { name: /reactivate/i });
    await userEvent.click(botao);

    await waitFor(() => expect(approveSkill).toHaveBeenCalled());
    // First argument only: react-query hands the mutationFn a second `{ client }` argument, so
    // `toHaveBeenCalledWith(name)` fails on a call that is entirely correct.
    expect(vi.mocked(approveSkill).mock.calls[0]?.[0]).toBe("chimera-thin-vertical-slice");
    // Retiring it again is not the offer here: the row is already retired.
    expect(retireSkill).not.toHaveBeenCalled();
  });

  it("does not offer it to a skill that is not retired", async () => {
    // The control. Without it, a button rendered unconditionally would pass the test above and
    // put "Reactivate" beside every active skill on the screen.
    vi.mocked(getSkills).mockResolvedValue({
      stats: [stat({ status: "active" })],
      retirement_candidates: [], cards_read: true,
    });
    renderWithProviders(<Skills />);

    await screen.findByText("chimera-thin-vertical-slice");
    expect(screen.queryByRole("button", { name: /reactivate/i })).toBeNull();
    // And the ordinary control is still there.
    expect(screen.getByRole("button", { name: /retire/i })).toBeTruthy();
  });

  it("a pending skill still gets approve, not reactivate", async () => {
    // Two different situations that share one route: a card held for review because its run was
    // tainted, and one the user retired themselves. The words must not swap.
    vi.mocked(getSkills).mockResolvedValue({
      stats: [stat({ status: "pending" })],
      retirement_candidates: [], cards_read: true,
    });
    renderWithProviders(<Skills />);

    await screen.findByText("chimera-thin-vertical-slice");
    expect(screen.getByRole("button", { name: /approve/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /reactivate/i })).toBeNull();
  });
});
