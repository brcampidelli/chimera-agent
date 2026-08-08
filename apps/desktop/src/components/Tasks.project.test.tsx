import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Tasks } from "@/components/Tasks";
import { getKanban, getProjects, startProject, stepProject } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addKanbanCard: vi.fn(),
  approveProject: vi.fn(),
  denyProject: vi.fn(),
  getKanban: vi.fn(),
  getProject: vi.fn(),
  getProjects: vi.fn(),
  moveKanbanCard: vi.fn(),
  removeKanbanCard: vi.fn(),
  startProject: vi.fn(),
  stepProject: vi.fn(),
  streamKanbanRun: vi.fn(),
}));

function project(over: Record<string, unknown> = {}) {
  return {
    id: "p1",
    status: "planning",
    iterations: 0,
    plan_approved: false,
    pending_card_id: null,
    note: "",
    max_iterations: 20,
    ...over,
  };
}

/**
 * This screen could approve a project and deny one, and could do neither of the two things that
 * come first: create it, or advance it. The HITL gate was here; the loop it gates was not.
 */
describe("Tasks — starting a project and moving it", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getKanban).mockResolvedValue({} as never);
    vi.mocked(getProjects).mockResolvedValue([] as never);
    vi.mocked(startProject).mockResolvedValue(project() as never);
    vi.mocked(stepProject).mockResolvedValue(project({ status: "running" }) as never);
  });

  it("starts a project from a spec path", async () => {
    // A path, not spec text: the spec decides whether the project is done, so it belongs in the
    // repository rather than in a text box nobody else can see.
    const user = userEvent.setup();
    renderWithProviders(<Tasks />);

    await user.type(await screen.findByLabelText(/path to a spec file/i), "specs/parser.yaml");
    await user.click(screen.getByRole("button", { name: /Start a project/ }));

    await waitFor(() => expect(startProject).toHaveBeenCalledOnce());
    expect(vi.mocked(startProject).mock.calls[0][0]).toEqual({ spec: "specs/parser.yaml" });
  });

  it("advances a project one iteration at a time", async () => {
    const user = userEvent.setup();
    vi.mocked(getProjects).mockResolvedValue([project()] as never);
    renderWithProviders(<Tasks />);

    await user.click(await screen.findByRole("button", { name: /One step/ }));
    await waitFor(() => expect(stepProject).toHaveBeenCalledOnce());
    expect(vi.mocked(stepProject).mock.calls[0][0]).toBe("p1");
  });

  it("does not offer to advance a finished project", async () => {
    // `done` is terminal. Offering to step it invites someone to restart work already accepted.
    vi.mocked(getProjects).mockResolvedValue([project({ status: "done" })] as never);
    renderWithProviders(<Tasks />);

    await screen.findByText("done");
    expect(screen.queryByRole("button", { name: /One step/ })).not.toBeInTheDocument();
  });

  it("asks for approval instead of a step while it is waiting for one", async () => {
    // Two buttons for "what happens next" is one too many, and the wrong one steps past the gate.
    vi.mocked(getProjects).mockResolvedValue(
      [project({ status: "awaiting_approval" })] as never,
    );
    renderWithProviders(<Tasks />);

    await screen.findByRole("button", { name: /Approve/ });
    expect(screen.queryByRole("button", { name: /One step/ })).not.toBeInTheDocument();
  });

  it("keeps the start form once a project exists", async () => {
    // The form is how you add a project, not a first-run placeholder — starting a second one must
    // not require getting rid of the first.
    vi.mocked(getProjects).mockResolvedValue([project()] as never);
    renderWithProviders(<Tasks />);

    expect(await screen.findByLabelText(/path to a spec file/i)).toBeInTheDocument();
  });
});
