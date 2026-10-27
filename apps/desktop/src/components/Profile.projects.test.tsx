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

  it("leaves out a bookmark whose folder is gone, and says so when none is left", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([
      row({ path: "/work/w-i-deleted-worktree", folder_exists: false, folder_key: "gone" }),
    ] as never);
    renderWithProviders(<Profile />);

    const panel = await projectsPanel();
    expect(await within(panel).findByText("No registered project has its folder on this computer.")).toBeInTheDocument();
    expect(within(panel).queryByText("w-i-deleted-worktree")).not.toBeInTheDocument();
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
    await user.click(await within(panel).findByRole("button", { name: "chimera-agent — Remove from list" }));

    await waitFor(() => expect(forgetCodeProject).toHaveBeenCalledTimes(2));
    expect(vi.mocked(forgetCodeProject).mock.calls.map((c) => c[0])).toEqual([
      "/work/chimera-agent",
      "/work/other/../chimera-agent",
    ]);
  });
});
