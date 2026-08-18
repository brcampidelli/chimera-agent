import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SessionSidebar } from "@/components/code/SessionSidebar";
import { listCodeSessions } from "@/lib/api";
import { addProject, readAliases, setAlias } from "@/lib/projects";
import { renderWithProviders } from "@/test/utils";

// The two row actions are stubbed but never driven here — this file is about the project grouping.
// They still have to exist: an unmocked export the component imports fails the whole file at mount,
// which reads as "the projects are broken" rather than "the mock is short two names".
vi.mock("@/lib/api", () => ({
  listCodeSessions: vi.fn(),
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
 */
describe("SessionSidebar — the projects", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    vi.mocked(listCodeSessions).mockResolvedValue([session()] as never);
  });

  it("shows a registered project that has no conversations yet", async () => {
    addProject("/home/me/virtual-sector");
    render();

    expect(await screen.findByText("chimera-agent")).toBeInTheDocument(); // from a conversation
    expect(screen.getByText("virtual-sector")).toBeInTheDocument(); // from the list alone
  });

  it("registering a project you have already talked about does not list it twice", async () => {
    // Union, not concatenation. The list and the conversations are two sources for the same set,
    // and the obvious way to combine them puts every project you both registered and used in the
    // sidebar twice — with the second copy holding none of its conversations.
    addProject("/home/me/chimera-agent");
    render();

    // Wait for the CONVERSATIONS, not for the name: the registered project renders immediately from
    // storage, so counting first would count one copy before the second could exist.
    await screen.findByText("what does this do?");
    expect(screen.getAllByText("chimera-agent")).toHaveLength(1);
  });

  it("adding a project selects it, rather than adding it and waiting", async () => {
    const user = userEvent.setup();
    const onProject = render();

    await user.click(await screen.findByRole("button", { name: "Add a project" }));
    await user.type(screen.getByRole("textbox", { name: "Add a project" }), "/home/me/lefran");
    await user.keyboard("{Enter}");

    await waitFor(() => expect(onProject).toHaveBeenCalledWith("/home/me/lefran"));
    expect(screen.getByText("lefran")).toBeInTheDocument();
  });

  it("calls a project what you called it", async () => {
    setAlias("/home/me/chimera-agent", "Chimera VPS");
    render();

    expect(await screen.findByText("Chimera VPS")).toBeInTheDocument();
    expect(screen.queryByText("chimera-agent")).not.toBeInTheDocument();
  });

  it("renames from the sidebar and remembers it", async () => {
    const user = userEvent.setup();
    render();

    await user.click(await screen.findByRole("button", { name: /Rename chimera-agent/ }));
    const field = screen.getByRole("textbox", { name: "Project name" });
    await user.type(field, "PassaPro");
    await user.click(within(field.closest("form") as HTMLElement).getByRole("button", { name: "Save" }));

    expect(await screen.findByText("PassaPro")).toBeInTheDocument();
    expect(readAliases()["/home/me/chimera-agent"]).toBe("PassaPro");
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
