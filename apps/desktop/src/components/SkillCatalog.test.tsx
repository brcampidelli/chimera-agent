import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Skills } from "@/components/Skills";
import {
  getSkillCatalog,
  installSkillBundle,
  setSkillBundleStatus,
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

const mockCatalog = vi.mocked(getSkillCatalog);

/** The portability filter (study 29, P7.1) lists every rating's name as an `<option>`, so a rating
 *  asserted by its words must be looked for on the ROW — the badge beside the name — and not in the
 *  menu that offers to filter by it. Same assertion as before, scoped to where it was always aimed. */
const ROW_ONLY = { ignore: "option, script, style" } as const;

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
  mockCatalog.mockResolvedValue(entries);
  window.history.replaceState(null, "", "#/settings?tab=skills&view=discover");
  renderWithProviders(<Skills />);
  await waitFor(() => expect(screen.getByText(entries[0].name as string)).toBeInTheDocument());
}

describe("the installable-skills catalogue", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says what a skill needs before offering to download it", async () => {
    await show(entry({ name: "manim-video", portability: "needs_heavy", requires: ["latex", "ffmpeg"] }));

    // The order is the point: this is the list that decides whether the thing runs at all on this
    // machine, and finding it out after the download means finding it out from a failure.
    expect(screen.getByText(/needs a GPU or gigabytes/i, ROW_ONLY)).toBeInTheDocument();
    expect(screen.getByText(/latex, ffmpeg/)).toBeInTheDocument();
  });

  it("does not present a skill written for another agent as one that works here", async () => {
    await show(
      entry({ name: "native-one" }),
      entry({ name: "sdlc-review", portability: "needs_adaptation", topic: "devops" }),
    );

    // Eighty names in one flat list would advertise eighty working features and deliver fewer.
    expect(screen.getByText(/written for another agent/i, ROW_ONLY)).toBeInTheDocument();
    expect(screen.getByText(/works here/i, ROW_ONLY)).toBeInTheDocument();
  });

  it("shows the licence, and shows an unreadable one differently", async () => {
    const user = userEvent.setup();
    await show(entry({ name: "unlicensed", license: "", permissive: false }));

    // An entry whose terms nobody could read is not the same as a permissive one, and these are
    // downloads: the difference belongs where the decision is made. The card's chip says "not
    // read" in amber (the shell's word); the panel, where the install button is, keeps this one.
    expect(screen.getByText(/license not read/i)).toBeInTheDocument();
    await user.click(screen.getByText("unlicensed"));
    expect(within(await screen.findByRole("dialog")).getByText(/no licence found/i)).toBeInTheDocument();
  });

  it("installs without switching on", async () => {
    const user = userEvent.setup();
    vi.mocked(installSkillBundle).mockResolvedValue({ name: "maps", status: "pending" } as never);
    await show(entry());

    await user.click(screen.getByRole("button", { name: /install/i }));

    await waitFor(() => expect(installSkillBundle).toHaveBeenCalledWith("maps"));
    // Downloading and consenting are two decisions: an installed skill's instructions reach the
    // prompt and can tell the agent to run the scripts that arrived with them.
    expect(setSkillBundleStatus).not.toHaveBeenCalled();
  });

  it("turns a skill off without deleting it", async () => {
    const user = userEvent.setup();
    vi.mocked(setSkillBundleStatus).mockResolvedValue({ name: "maps", status: "inactive" } as never);
    await show(entry({ installed: "active" }));

    await user.click(screen.getByRole("button", { name: /^on$/i }));

    // Off is not uninstalled. Trying several and leaving two running is the normal way to use
    // these, and making "off" mean "delete" would charge a download for every change of mind.
    expect(setSkillBundleStatus).toHaveBeenCalledWith("maps", "inactive");
  });

  it("says where these come from, once, before the list", async () => {
    await show(entry());

    // Nothing here ships with the app, and what lands on disk arrives from its author under the
    // author's terms. A person downloading other people's code should be told that plainly.
    expect(screen.getByText(/None of these ship with Chimera/i)).toBeInTheDocument();
  });

  it("reports a refused install in the words the server used", async () => {
    const user = userEvent.setup();
    vi.mocked(installSkillBundle).mockRejectedValue(new Error("the skill has more than 200 files"));
    await show(entry());

    await user.click(screen.getByRole("button", { name: /install/i }));

    // Which limit, which file, which host — not "400 Bad Request".
    await waitFor(() =>
      expect(screen.getByText(/more than 200 files/i)).toBeInTheDocument(),
    );
  });

  it("filters by what a skill does, not only by its name", async () => {
    const user = userEvent.setup();
    await show(
      entry({ name: "maps", description: "Geocode, routes, timezones." }),
      entry({ name: "himalaya", description: "IMAP and SMTP email from the terminal.", topic: "email" }),
    );

    await user.type(screen.getByLabelText(/search/i), "email");

    expect(screen.getByText("himalaya")).toBeInTheDocument();
    expect(screen.queryByText("maps")).not.toBeInTheDocument();
  });

  it("credits whoever the upstream credits", async () => {
    await show(entry({ author: "Jim Liu (宝玉)" }));

    // Several of these are ports of other people's work. Carrying the field means the credit
    // reaches a reader instead of stopping at the repository it was copied from.
    expect(screen.getByText(/Jim Liu/)).toBeInTheDocument();
  });
});

/**
 * The card's own text sat flush against its border while its heading did not.
 *
 * `Panel` insets its TITLE BAR by `px-4` and gives its body nothing, so a panel whose content forgot
 * to pad disagreed with its own heading by 16px — measured in the running app before the fix: the
 * title 17px from the panel's left edge, "82 disponíveis · 0 instaladas aqui" at 1px.
 *
 * The catalogue no longer sits in a `Panel`: it is Discover on the catalog shell, where the screen's
 * heading and every section's heading live in one container. The same promise, asserted where it
 * lives now: between that container and either heading there is no horizontal inset on one side
 * that the other lacks. Asserting classes rather than positions, because jsdom has no layout — and
 * an extra `px-*` on one side is exactly the shape the original defect had.
 */
describe("SkillCatalog — the body is inset like its heading", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCatalog.mockResolvedValue([entry()]);
  });

  it("puts the content inside the panel's own margin", async () => {
    window.history.replaceState(null, "", "#/settings?tab=skills&view=discover");
    renderWithProviders(<Skills />);
    // The card, not /maps/: the card's source line ("x/y/tree/main/skills/maps") matches that too.
    await screen.findByText("maps");

    const titulo = screen.getByRole("heading", { level: 2, name: "Skills" });
    const secao = screen.getByRole("heading", { level: 3, name: "productivity" });
    const comum = (() => {
      let el: HTMLElement | null = titulo;
      while (el && !el.contains(secao)) el = el.parentElement;
      return el;
    })();
    const inset = (from: HTMLElement) => {
      const found: string[] = [];
      for (let el: HTMLElement | null = from; el && el !== comum; el = el.parentElement)
        found.push(...el.className.split(/\s+/).filter((c) => /^(px|pl|ps|mx|ml|ms)-/.test(c)));
      return found;
    };

    expect(comum, "the heading and the list no longer share a container").not.toBeNull();
    expect(inset(secao), "the section is inset where the heading is not").toEqual(inset(titulo));
  });
});
