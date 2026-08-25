import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SkillCatalog } from "@/components/SkillCatalog";
import {
  getSkillCatalog,
  installSkillBundle,
  setSkillBundleStatus,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";
import type { CatalogEntry } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  getSkillCatalog: vi.fn(),
  getSkillBundles: vi.fn(async () => []),
  installSkillBundle: vi.fn(),
  setSkillBundleStatus: vi.fn(),
  uninstallSkillBundle: vi.fn(),
}));

const mockCatalog = vi.mocked(getSkillCatalog);

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
  renderWithProviders(<SkillCatalog />);
  await waitFor(() => expect(screen.getByText(entries[0].name as string)).toBeInTheDocument());
}

describe("the installable-skills catalogue", () => {
  beforeEach(() => vi.clearAllMocks());

  it("says what a skill needs before offering to download it", async () => {
    await show(entry({ name: "manim-video", portability: "needs_heavy", requires: ["latex", "ffmpeg"] }));

    // The order is the point: this is the list that decides whether the thing runs at all on this
    // machine, and finding it out after the download means finding it out from a failure.
    expect(screen.getByText(/needs a GPU or gigabytes/i)).toBeInTheDocument();
    expect(screen.getByText(/latex, ffmpeg/)).toBeInTheDocument();
  });

  it("does not present a skill written for another agent as one that works here", async () => {
    await show(
      entry({ name: "native-one" }),
      entry({ name: "sdlc-review", portability: "needs_adaptation", topic: "devops" }),
    );

    // Eighty names in one flat list would advertise eighty working features and deliver fewer.
    expect(screen.getByText(/written for another agent/i)).toBeInTheDocument();
    expect(screen.getByText(/works here/i)).toBeInTheDocument();
  });

  it("shows the licence, and shows an unreadable one differently", async () => {
    await show(entry({ name: "unlicensed", license: "", permissive: false }));

    // An entry whose terms nobody could read is not the same as a permissive one, and these are
    // downloads: the difference belongs where the decision is made.
    expect(screen.getByText(/no licence found/i)).toBeInTheDocument();
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
 * `Panel` insets its TITLE BAR by `px-4` and gives its body nothing, so a panel whose content
 * forgets to pad disagrees with its own heading by 16px. Measured in the running app before the
 * fix: the title 17px from the panel's left edge, "82 disponíveis · 0 instaladas aqui" at 1px.
 *
 * Asserting a class rather than a position, because jsdom has no layout — but the class is the
 * thing that was missing, and this is the panel it was missing from. The padding lives here rather
 * than in `Panel` on purpose: the divider lines in a LIST panel are meant to run edge to edge, and
 * padding the shared wrapper would inset those too.
 *
 * Worth recording how this was found, because it is the second lesson: three measurements were
 * taken and the first two produced five false positives between them. Measuring the ELEMENT BOX
 * called four correct panels broken; treating a full-bleed band as a leaf called a fifth broken.
 * Only measuring where the GLYPHS are — `Range.getBoundingClientRect` over text nodes — agreed with
 * what could be seen on screen, and it found exactly one defect.
 */
describe("SkillCatalog — the body is inset like its heading", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCatalog.mockResolvedValue([entry()]);
  });

  it("puts the content inside the panel's own margin", async () => {
    const { container } = renderWithProviders(<SkillCatalog />);
    await screen.findByText(/maps/);

    const titulo = container.querySelector("h2");
    const barra = titulo?.parentElement;
    const corpo = barra?.nextElementSibling?.firstElementChild;

    expect(barra?.className, "the title bar stopped carrying the inset this is measured against").toContain("px-4");
    expect(corpo?.className, "the panel body is flush against the border again").toContain("px-4");
  });
});
