import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionSidebar } from "@/components/code/SessionSidebar";
import {
  deleteCodeProject,
  forgetCodeProject,
  listCodeProjects,
  listCodeSessions,
  registerCodeProject,
} from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

// The row actions are stubbed but not all driven here — this file is about the project grouping.
// They still have to exist: an unmocked export the component imports fails the whole file at mount,
// which reads as "the projects are broken" rather than "the mock is short two names".
vi.mock("@/lib/api", () => ({
  listCodeSessions: vi.fn(),
  listCodeProjects: vi.fn(),
  registerCodeProject: vi.fn(),
  forgetCodeProject: vi.fn(),
  deleteCodeProject: vi.fn(),
  deleteCodeSession: vi.fn(),
  forkCodeSession: vi.fn(),
  getCodeSessionRaw: vi.fn(),
}));

function session(over: Record<string, unknown> = {}) {
  return {
    id: "s1",
    title: "what does this do?",
    workspace: "/home/me/chimera-agent",
    turns: 2,
    updated_at: 0,
    ...over,
  };
}

function render(onProject = vi.fn()) {
  renderWithProviders(
    <SessionSidebar
      workspace=""
      activeSession={null}
      onResume={vi.fn()}
      onNew={vi.fn()}
      onProject={onProject}
    />,
  );
  return onProject;
}

/**
 * The sidebar has always grouped by project — but a project could only appear by having already
 * been talked about, and its name was the last segment of its path. So you could not add the
 * project you were about to start on, and two checkouts called `frontend` read as one.
 *
 * The list now comes from the server rather than from this browser's storage, which is what lets it
 * survive a reinstall and be seeded from outside. The grouping is unchanged: registered projects
 * and projects with conversations are UNIONED, so nothing disappears for not being on the list.
 */
describe("SessionSidebar — the projects", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([session()] as never);
    vi.mocked(listCodeProjects).mockResolvedValue([] as never);
  });

  it("shows a registered project that has no conversations yet", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([
      { path: "/home/me/virtual-sector", alias: "" },
    ] as never);
    render();

    expect(await screen.findByText("chimera-agent")).toBeInTheDocument(); // from a conversation
    expect(await screen.findByText("virtual-sector")).toBeInTheDocument(); // from the list alone
  });

  it("registering a project you have already talked about does not list it twice", async () => {
    // Union, not concatenation. The list and the conversations are two sources for the same set,
    // and the obvious way to combine them puts every project you both registered and used in the
    // sidebar twice — with the second copy holding none of its conversations.
    vi.mocked(listCodeProjects).mockResolvedValue([
      { path: "/home/me/chimera-agent", alias: "" },
    ] as never);
    render();

    await screen.findByText("what does this do?");
    expect(screen.getAllByText("chimera-agent")).toHaveLength(1);
  });

  it("adding a project selects it, rather than adding it and waiting", async () => {
    const user = userEvent.setup();
    vi.mocked(registerCodeProject).mockResolvedValue([
      { path: "/home/me/lefran", alias: "" },
    ] as never);
    const onProject = render();

    await user.click(await screen.findByRole("button", { name: "Add a project" }));
    await user.type(screen.getByRole("textbox", { name: "Add a project" }), "/home/me/lefran");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(onProject).toHaveBeenCalledWith("/home/me/lefran"));
    expect(await screen.findByText("lefran")).toBeInTheDocument();
  });

  it("calls a project what you called it", async () => {
    vi.mocked(listCodeProjects).mockResolvedValue([
      { path: "/home/me/chimera-agent", alias: "Chimera VPS" },
    ] as never);
    render();

    expect(await screen.findByText("Chimera VPS")).toBeInTheDocument();
    expect(screen.queryByText("chimera-agent")).not.toBeInTheDocument();
  });

  it("renames from the sidebar and keeps the name on the server", async () => {
    const user = userEvent.setup();
    vi.mocked(registerCodeProject).mockResolvedValue([
      { path: "/home/me/chimera-agent", alias: "PassaPro" },
    ] as never);
    render();

    await user.click(await screen.findByRole("button", { name: /Rename chimera-agent/ }));
    const field = screen.getByRole("textbox", { name: "Project name" });
    await user.type(field, "PassaPro");
    await user.click(
      within(field.closest("form") as HTMLElement).getByRole("button", { name: "Save" }),
    );

    expect(await screen.findByText("PassaPro")).toBeInTheDocument();
    expect(registerCodeProject).toHaveBeenCalledWith("/home/me/chimera-agent", "PassaPro");
  });

  it("deleting a project with no conversations removes the bookmark, not transcripts", async () => {
    // The button did nothing at all on these rows: it asked the route that deletes conversations to
    // delete none. That was invisible while a project could only exist by having been used — and
    // ordinary the moment you can add one you have not worked in yet.
    const user = userEvent.setup();
    vi.mocked(listCodeProjects).mockResolvedValue([
      { path: "/home/me/virtual-sector", alias: "" },
    ] as never);
    vi.mocked(forgetCodeProject).mockResolvedValue([] as never);
    render();

    await user.click(await screen.findByRole("button", { name: /Delete the project virtual-sector/ }));
    expect(await screen.findByText(/Nothing is deleted/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(forgetCodeProject).toHaveBeenCalledWith("/home/me/virtual-sector"));
    expect(deleteCodeProject).not.toHaveBeenCalled();
  });

  it("deleting a project that has conversations still deletes those", async () => {
    const user = userEvent.setup();
    vi.mocked(deleteCodeProject).mockResolvedValue({ deleted: 1 } as never);
    render();

    await user.click(await screen.findByRole("button", { name: /Delete the project chimera-agent/ }));
    expect(await screen.findByText(/folder on disk is not touched/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() =>
      expect(deleteCodeProject).toHaveBeenCalledWith("/home/me/chimera-agent"),
    );
    expect(forgetCodeProject).not.toHaveBeenCalled();
  });

  it("does not offer to rename the group that is the absence of a project", async () => {
    // The default group is where conversations with no workspace land. Naming it would name a
    // hole rather than a project, and the name would apply to every future homeless conversation.
    vi.mocked(listCodeSessions).mockResolvedValue([session({ workspace: "" })] as never);
    render();

    await screen.findByText("Default project");
    expect(screen.queryByRole("button", { name: /^Rename / })).not.toBeInTheDocument();
  });
});
