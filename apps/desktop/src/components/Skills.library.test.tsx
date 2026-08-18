import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Skills } from "@/components/Skills";
import { getSkillLibrary, getSkillLibraryCard, getSkills, importSkillLibraryCard } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getSkills: vi.fn(),
  approveSkill: vi.fn(),
  retireSkill: vi.fn(),
  getSkillLibrary: vi.fn(),
  getSkillLibraryCard: vi.fn(),
  importSkillLibraryCard: vi.fn(),
}));

function card(over: Record<string, unknown> = {}) {
  return {
    name: "verify-before-claiming",
    description: "Run the check that would fail if the work had not happened.",
    version: "0.1.0",
    kind: "pattern",
    stage: "verify",
    topic: "software-dev",
    triggers: ["about to report success"],
    license: "Apache-2.0",
    body: "",
    imported: false,
    ...over,
  };
}

/**
 * The screen showed learned skills only — and learned skills are distilled from the user's own
 * verified runs, so a fresh install saw an empty list and nothing else. The twenty-three curated
 * cards that ship in the box had no route to reach it by.
 */
describe("Skills — the curated library", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getSkills).mockResolvedValue({ stats: [], retirement_candidates: [] } as never);
    vi.mocked(getSkillLibrary).mockResolvedValue([
      card(),
      card({ name: "chimera-thin-vertical-slice", description: "One slice end to end.", stage: "build" }),
    ] as never);
  });

  it("lists the shipped cards next to the learned ones", async () => {
    renderWithProviders(<Skills />);

    expect(await screen.findByText("verify-before-claiming")).toBeInTheDocument();
    expect(screen.getByText("chimera-thin-vertical-slice")).toBeInTheDocument();
    // The learned panel is still there and still honestly empty.
    expect(screen.getByText(/No learned skills yet/)).toBeInTheDocument();
  });

  it("groups by where in the work a card applies, in reading order", async () => {
    // Define -> Verify -> Ship is the order of the work. Alphabetically it is Define, Ship, Verify,
    // which reads as advice to release something before checking it.
    //
    // These three stages specifically: the first version of this test used the default fixture's
    // Build and Verify, which are in the same order under BOTH rules — so it passed against a
    // deliberately alphabetical implementation and was asserting nothing.
    vi.mocked(getSkillLibrary).mockResolvedValue([
      card({ name: "ships-it", stage: "ship" }),
      card({ name: "defines-it", stage: "define" }),
      card({ name: "verifies-it", stage: "verify" }),
    ] as never);
    renderWithProviders(<Skills />);

    await screen.findByText("defines-it");
    const headings = screen.getAllByText(/^(Define|Build|Verify|Review|Ship)$/).map((n) => n.textContent);
    expect(headings).toEqual(["Define", "Verify", "Ship"]);
  });

  it("says the library is missing rather than that you have none", async () => {
    // These ship; they are never earned. "No skills yet" would tell a user to go and do something
    // that cannot possibly produce them.
    vi.mocked(getSkillLibrary).mockResolvedValue([] as never);
    renderWithProviders(<Skills />);

    expect(await screen.findByText(/This build ships no curated cards/)).toBeInTheDocument();
  });

  it("opens a card and shows the body the agent actually reads", async () => {
    const user = userEvent.setup();
    vi.mocked(getSkillLibraryCard).mockResolvedValue(
      card({ body: "## Trigger\nYou are about to say a task is complete." }) as never,
    );
    renderWithProviders(<Skills />);

    await user.click(await screen.findByText("verify-before-claiming"));

    expect(await screen.findByText(/You are about to say a task is complete/)).toBeInTheDocument();
    // The body is fetched on open, not with the list: twenty-three of them is a quarter of a
    // megabyte spent to draw a column of names.
    expect(getSkillLibraryCard).toHaveBeenCalledWith("verify-before-claiming");
  });

  it("imports a card, so the screen is not a display case", async () => {
    const user = userEvent.setup();
    // One card, so "the Import button" is unambiguous. With the two-card fixture the first one in
    // the DOM belongs to the Build group, which renders before Verify — a correct click on the
    // wrong row, and the kind of index-based assertion that passes for the wrong reason.
    vi.mocked(getSkillLibrary).mockResolvedValue([card()] as never);
    vi.mocked(importSkillLibraryCard).mockResolvedValue({
      imported: true,
      name: "verify-before-claiming",
      status: "active",
    } as never);
    renderWithProviders(<Skills />);

    await screen.findByText("verify-before-claiming");
    await user.click(screen.getByRole("button", { name: "Import" }));

    // On the first ARGUMENT, not the whole call: react-query hands a mutationFn a second context
    // argument, so `toHaveBeenCalledWith(name)` fails on a mutation that fired correctly.
    await waitFor(() => expect(importSkillLibraryCard).toHaveBeenCalledTimes(1));
    expect(vi.mocked(importSkillLibraryCard).mock.calls[0][0]).toBe("verify-before-claiming");
  });

  it("stops offering an import that already happened", async () => {
    vi.mocked(getSkillLibrary).mockResolvedValue([card({ imported: true })] as never);
    renderWithProviders(<Skills />);

    expect(await screen.findByText("Imported")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Import" })).not.toBeInTheDocument();
  });
});
