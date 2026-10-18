import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Skills } from "@/components/Skills";
import {
  checkSkillBundleUpdate,
  getSkillBundles,
  getSkillCatalog,
  installSkillBundle,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { CatalogEntry } from "@/lib/types";

// The catalogue is Discover on the Skills screen now (study 04, PR 5), so the mock carries the whole
// surface that screen touches: a missing export throws during render and every assertion below
// would then fail about a function none of them test.
vi.mock("@/lib/api", () => ({
  getSkillCatalog: vi.fn(),
  getSkillBundles: vi.fn(async () => []),
  installSkillBundle: vi.fn(),
  setSkillBundleStatus: vi.fn(),
  uninstallSkillBundle: vi.fn(),
  checkSkillBundleUpdate: vi.fn(),
  getEffectiveSkills: vi.fn(async () => ({ bundles: [], bundle_text: "", cards_read: false, cards: [], cards_k: 0 })),
  getSkills: vi.fn(async () => ({ stats: [], retirement_candidates: [], cards_read: false })),
  approveSkill: vi.fn(),
  retireSkill: vi.fn(),
  getSkillLibrary: vi.fn(async () => []),
  getSkillLibraryCard: vi.fn(),
  importSkillLibraryCard: vi.fn(),
  importSkill: vi.fn(),
  getSkillBundleText: vi.fn(),
  getPinnedCatalog: vi.fn(async () => ({ kind: "skills", repo: "brcampidelli/chimera-skills", sha: "b".repeat(40), items: [], available: true, reason: null, problems: [] })),
}));

function entry(over: Partial<CatalogEntry> = {}): CatalogEntry {
  return {
    name: "maps",
    description: "Geocode, routes, timezones.",
    topic: "productivity",
    license: "MIT",
    permissive: true,
    portability: "native",
    requires: [],
    note: "",
    author: "",
    homepage: "https://github.com/x/y/tree/main/skills/maps",
    installed: "",
    ...over,
  } as CatalogEntry;
}

async function show(...entries: CatalogEntry[]) {
  vi.mocked(getSkillCatalog).mockResolvedValue(entries);
  window.history.replaceState(null, "", "#/settings?tab=skills&view=discover");
  renderWithProviders(<Skills />);
  await waitFor(() => expect(screen.getByText(entries[0].name as string)).toBeInTheDocument());
}

/** Study 29, P7.1: the catalogue is filtered and ordered by the reader, and an installed skill can
 *  be checked against its source and updated — back to switched off. */
describe("the catalogue's filters, order and updates", () => {
  beforeEach(() => vi.clearAllMocks());

  it("filters by state, so the installed ones can be found among eighty", async () => {
    const user = userEvent.setup();
    await show(
      entry({ name: "maps" }),
      entry({ name: "himalaya", installed: "pending", topic: "email" }),
      entry({ name: "notion", installed: "active", topic: "productivity" }),
    );

    // The state filter is the shell's own now (Filters › State), the same values as the old select.
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Pending" }));

    expect(screen.getByText("himalaya")).toBeInTheDocument();
    expect(screen.queryByText("maps")).not.toBeInTheDocument();
    expect(screen.queryByText("notion")).not.toBeInTheDocument();

    await user.click(screen.getByRole("menuitemcheckbox", { name: "Pending" }));
    await user.click(screen.getByRole("menuitemcheckbox", { name: "Not installed" }));
    expect(screen.getByText("maps")).toBeInTheDocument();
    expect(screen.queryByText("himalaya")).not.toBeInTheDocument();
  });

  it("filters by whether a skill works here", async () => {
    const user = userEvent.setup();
    await show(
      entry({ name: "maps" }),
      entry({ name: "manim-video", portability: "needs_heavy" }),
    );

    await user.selectOptions(screen.getByLabelText(/^portability/i), "needs_heavy");

    expect(screen.getByText("manim-video")).toBeInTheDocument();
    expect(screen.queryByText("maps")).not.toBeInTheDocument();
  });

  it("orders by name in one list, without the topic headings", async () => {
    const user = userEvent.setup();
    await show(
      entry({ name: "zeta", topic: "alpha-topic" }),
      entry({ name: "alpha", topic: "zulu-topic" }),
    );
    // By topic first: alpha-topic's heading, then zeta under it, before alpha.
    expect(screen.getByText("alpha-topic")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText(/^order/i), "name");

    expect(screen.queryByText("alpha-topic")).not.toBeInTheDocument();
    const names = screen.getAllByText(/^(alpha|zeta)$/).map((el) => el.textContent);
    expect(names).toEqual(["alpha", "zeta"]);
  });

  // Review of P7.1: a switch thrown while no switched-on skill reached a prompt waits for a new one.
  it("says why a skill someone switched on before reads as off", async () => {
    vi.mocked(getSkillBundles).mockResolvedValue([
      { name: "maps", status: "pending", reconfirm: true, ref: "a".repeat(40) },
    ] as never);
    await show(entry({ installed: "pending" }));

    await screen.findByText(/reaches nothing until you switch it on again/i);
    expect(screen.getByRole("button", { name: /^off$/i })).toBeInTheDocument();
  });

  it("names the commit an installed skill came from", async () => {
    vi.mocked(getSkillBundles).mockResolvedValue([
      { name: "maps", status: "active", ref: "a".repeat(40), committed_at: "2026-09-01T10:00:00Z" },
    ] as never);
    const user = userEvent.setup();
    await show(entry({ installed: "active" }));

    // The commit rides on the card's source line, and the panel names it with its date.
    await waitFor(() => expect(screen.getByText(/@aaaaaaa/)).toBeInTheDocument());
    await user.click(screen.getByText("maps"));
    const panel = await screen.findByRole("dialog");
    expect(within(panel).getByText(/aaaaaaa/)).toBeInTheDocument();
    expect(within(panel).getByText(/2026-09-01/)).toBeInTheDocument();
  });

  it("checks for an update only when asked, and updating sends it back to off", async () => {
    const user = userEvent.setup();
    vi.mocked(checkSkillBundleUpdate).mockResolvedValue({
      name: "maps",
      current_ref: "a".repeat(40),
      current_date: "2026-09-01T10:00:00Z",
      latest_ref: "b".repeat(40),
      latest_date: "2026-09-20T10:00:00Z",
      changed: true,
    } as never);
    vi.mocked(installSkillBundle).mockResolvedValue({ name: "maps", status: "pending" } as never);
    await show(entry({ installed: "active" }));

    // The check lives in the skill's panel now. Opening it asks nothing of the source either: a
    // request to GitHub is the owner's click.
    await user.click(screen.getByText("maps"));
    await screen.findByRole("dialog");
    expect(checkSkillBundleUpdate).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: /check for update/i }));

    const note = await screen.findByText(/newer at the source/i);
    expect(within(note).getByText(/bbbbbbb/, { exact: false })).toBeInTheDocument();
    expect(note.textContent).toMatch(/switches the skill off/i);

    await user.click(screen.getByRole("button", { name: /^update$/i }));
    await waitFor(() => expect(installSkillBundle).toHaveBeenCalledWith("maps", true));
  });

  it("offers no update when the source has nothing newer", async () => {
    const user = userEvent.setup();
    vi.mocked(checkSkillBundleUpdate).mockResolvedValue({
      name: "maps",
      current_ref: "a".repeat(40),
      current_date: "2026-09-20T10:00:00Z",
      latest_ref: "a".repeat(40),
      latest_date: "2026-09-01T10:00:00Z",
      changed: false,
    } as never);
    await show(entry({ installed: "active" }));

    await user.click(screen.getByText("maps"));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: /check for update/i }));

    await screen.findByText(/up to date/i);
    expect(screen.queryByRole("button", { name: /^update$/i })).not.toBeInTheDocument();
  });

  // Review of P7.1: the check compares the folder's files, so "unknown" is about the installed
  // commit (not a date) — and an unknown is never shown as "up to date".
  it("says it cannot tell when the installed copy names no commit the source has", async () => {
    const user = userEvent.setup();
    vi.mocked(checkSkillBundleUpdate).mockResolvedValue({
      name: "maps",
      current_ref: "main",
      current_date: "",
      latest_ref: "b".repeat(40),
      latest_date: "2026-09-20T10:00:00Z",
      changed: null,
    } as never);
    await show(entry({ installed: "active" }));

    await user.click(screen.getByText("maps"));
    await screen.findByRole("dialog");
    await user.click(screen.getByRole("button", { name: /check for update/i }));

    await screen.findByText(/whether its files differ cannot be told/i);
    expect(screen.queryByText(/up to date/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^update$/i })).toBeInTheDocument();
  });
});
