import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Profile } from "@/components/Profile";
import { forgetCodeProject, getInstructions, getMemoryLayers, getMemoryProfile, listCodeProjects } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  editMemory: vi.fn(),
  forgetCodeProject: vi.fn(),
  getInstructions: vi.fn(),
  getMemoryLayers: vi.fn(),
  getMemoryProfile: vi.fn(),
  listCodeProjects: vi.fn(),
  putInstructions: vi.fn(),
}));

/** One bookmark, as the server now sends it. Defaults describe a folder that is there. */
function row(over: Record<string, unknown>) {
  return { alias: "", pinned: false, hidden: false, shell_granted: false, folder_exists: true, folder_key: "", ...over };
}

/**
 * The project list on the profile lists only folders that are still on this computer, shows a folder
 * once however many bookmarks name it, and lets the person take a bookmark off the list.
 *
 * The bug: a worktree deleted after use stayed listed, `chimera-agent` appeared twice, and there was
 * no way to remove any of them. Removing forgets the bookmark only, so the folder and its
 * conversations stay where they are.
 */
beforeEach(() => {
  localStorage.setItem("chimera.lang", "en");
  vi.clearAllMocks();
  vi.mocked(getInstructions).mockResolvedValue({ name: "", language: "English", instructions: "" } as never);
  vi.mocked(getMemoryLayers).mockResolvedValue({ total: 0, clean: 0, tainted: 0, layers: [], by_source: [], semantic_embeddings_enabled: false } as never);
  vi.mocked(getMemoryProfile).mockResolvedValue({ profile: "", persona: [] } as never);
  vi.mocked(forgetCodeProject).mockResolvedValue([] as never);
});

async function projectsPanel() {
  const heading = await screen.findByText("Registered projects");
  return heading.closest("div")!.parentElement as HTMLElement;
}

describe("the registered projects on the profile", () => {
  it("lists a project whose folder is there, under its name", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([row({ path: "/work/atlas", alias: "Atlas", folder_key: "a" })] as never);
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    expect(await within(panel).findByText("Atlas")).toBeInTheDocument();
  });

  it("keeps a bookmark whose folder is gone in a collapsed section of its own, and says none is listed", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([
      row({ path: "/work/w-i-deleted-worktree", folder_exists: false, folder_state: "gone", folder_key: "gone" }),
    ] as never);
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    expect(await within(panel).findByText("No registered project has its folder on this computer.")).toBeInTheDocument();
    // Not dropped: a bookmark that vanished without a trace could not be cleaned up.
    const section = within(panel).getByText("Folders not found (1)").closest("details") as HTMLDetailsElement;
    expect(section.open).toBe(false);
    expect(within(section).getByText("w-i-deleted-worktree")).toBeInTheDocument();
    expect(within(section).getByText("Not on this computer")).toBeInTheDocument();
  });

  it("does not claim a network path is missing: it is listed as not checked", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([
      row({ path: "\\fileserver\share\repo", folder_exists: false, folder_state: "unverifiable", folder_key: "unc" }),
    ] as never);
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    const section = (await within(panel).findByText("Folders not found (1)")).closest("details") as HTMLDetailsElement;
    expect(within(section).getByText("Network or device path: not checked")).toBeInTheDocument();
    expect(within(section).queryByText("Not on this computer")).not.toBeInTheDocument();
  });

  it("shows one folder once, however many bookmarks name it", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([
      row({ path: "/work/chimera-agent", folder_key: "same" }),
      row({ path: "/work/other/../chimera-agent", folder_key: "same" }),
    ] as never);
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    await within(panel).findByText("chimera-agent");
    expect(within(panel).getAllByText("chimera-agent")).toHaveLength(1);
  });

  it("does not list a project the owner has already removed from the lists", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([row({ path: "/work/old", hidden: true, folder_key: "old" })] as never);
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    expect(await within(panel).findByText("No registered project has its folder on this computer.")).toBeInTheDocument();
  });

  it("removes a project from the list, forgetting every spelling of its folder and nothing else", async () => {
    const user = userEvent.setup();
    vi.mocked(listCodeProjects).mockResolvedValue([
      row({ path: "/work/chimera-agent", folder_key: "same" }),
      row({ path: "/work/other/../chimera-agent", folder_key: "same" }),
    ] as never);
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    await user.click(
      await within(panel).findByRole("button", { name: "chimera-agent — Remove from list, with its command grant and pin" }),
    );

    await waitFor(() => expect(forgetCodeProject).toHaveBeenCalledTimes(2));
    expect(vi.mocked(forgetCodeProject).mock.calls.map((c) => c[0])).toEqual([
      "/work/chimera-agent",
      "/work/other/../chimera-agent",
    ]);
  });

  it("says so, and reloads the list, when a removal fails part way through", async () => {
    const user = userEvent.setup();
    vi.mocked(listCodeProjects).mockResolvedValue([
      row({ path: "/work/chimera-agent", folder_key: "same" }),
      row({ path: "/work/other/../chimera-agent", folder_key: "same" }),
    ] as never);
    vi.mocked(forgetCodeProject)
      .mockResolvedValueOnce([] as never)
      .mockRejectedValueOnce(new Error("network down"));
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    const before = vi.mocked(listCodeProjects).mock.calls.length;
    await user.click(
      await within(panel).findByRole("button", { name: "chimera-agent — Remove from list, with its command grant and pin" }),
    );

    expect(await within(panel).findByRole("alert")).toHaveTextContent(
      "Could not remove everything. The list was reloaded; check it.",
    );
    // Whatever happened, the list is read again rather than shown from before the attempt.
    await waitFor(() => expect(vi.mocked(listCodeProjects).mock.calls.length).toBeGreaterThan(before));
  });
});
