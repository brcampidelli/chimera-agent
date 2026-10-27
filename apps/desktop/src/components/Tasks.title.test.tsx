import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Tasks } from "@/components/Tasks";
import { getAgentRegistry, getKanban, getProjects } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addKanbanCard: vi.fn(),
  approveProject: vi.fn(),
  denyProject: vi.fn(),
  getAgentRegistry: vi.fn(),
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
    id: "84feb3f3",
    status: "planning",
    iterations: 0,
    plan_approved: false,
    pending_card_id: null,
    note: "",
    max_iterations: 20,
    title: "",
    ...over,
  };
}

/**
 * The project list named each project by its id alone, so the person had to remember which spec
 * `84feb3f3` was. The spec's name is the project's own title; the id stays beside it, because the
 * board and the logs use it.
 */
describe("Tasks — a project is named by its spec", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getAgentRegistry).mockResolvedValue([]);
    vi.mocked(getKanban).mockResolvedValue({} as never);
  });

  it("shows the title as the project's name, with its id beside it", async () => {
    vi.mocked(getProjects).mockResolvedValue([project({ title: "Loja de café — catálogo" })] as never);
    renderWithProviders(<Tasks />);

    expect(await screen.findByRole("button", { name: "Loja de café — catálogo" })).toBeInTheDocument();
    expect(screen.getByText("84feb3f3")).toBeInTheDocument();
  });

  it("falls back to the id alone when the spec has no readable name", async () => {
    vi.mocked(getProjects).mockResolvedValue([project({ title: "" })] as never);
    renderWithProviders(<Tasks />);

    const button = await screen.findByRole("button", { name: "84feb3f3" });
    expect(button).toBeInTheDocument();
    // Shown once: a title-less project has no second label to repeat the id in.
    expect(screen.getAllByText("84feb3f3")).toHaveLength(1);
  });
});
