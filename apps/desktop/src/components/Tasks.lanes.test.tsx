import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Tasks } from "@/components/Tasks";
import { getAgentRegistry, getKanban, getProjects } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

// Hand-rolled rather than the shared `code-api-mock`: that one covers the Code screen, and the
// board reaches for a different set entirely (kanban, projects, the agent registry).
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

/**
 * The board stopped warning about its own default.
 *
 * The lane field starts on `solve`. `known` is the AGENT registry, and `solve` is not an agent — the
 * server registers it as a lane itself, next to `crew`. So an untouched form accused its own default
 * of not existing, and pointed at the one runner that cannot be missing.
 *
 * What kept it alive is the shape of the condition, not the condition itself: it also required
 * `known.length > 0`, so a fresh install saw nothing. Registering your first agent is what made the
 * app start complaining about the lane it had been using all along — a warning that arrives as a
 * reward for setting something up correctly reads like the setup broke something.
 *
 * `tests/test_builtin_lanes_agree.py` is the other half: it parses the server's runner dict and
 * fails if these names drift, in either direction. A stale name here would silence the warning for
 * a lane that really is missing, which is the failure the warning exists to prevent.
 */
describe("Tasks — the lane field", () => {
  beforeEach(() => {
    vi.mocked(getProjects).mockResolvedValue([]);
    vi.mocked(getKanban).mockResolvedValue({ cards: [] } as Awaited<ReturnType<typeof getKanban>>);
    // At least one agent, because with none the old check was silent and the bug invisible.
    vi.mocked(getAgentRegistry).mockResolvedValue([
      { id: "revisor-html", name: "Revisor de HTML", instructions: "", model: "" },
    ]);
  });

  async function lane() {
    renderWithProviders(<Tasks />);
    return await screen.findByLabelText(/Who works it|Quem trabalha nisso/i);
  }

  it("says nothing about the default it ships with", async () => {
    const field = await lane();

    expect((field as HTMLInputElement).value).toBe("solve");
    await waitFor(() => expect(getAgentRegistry).toHaveBeenCalled());
    expect(screen.queryByText(/no agent with this id/i)).toBeNull();
  });

  it("accepts the other built-in lane too", async () => {
    const user = userEvent.setup();
    const field = await lane();
    await user.clear(field);
    await user.type(field, "crew");

    expect(screen.queryByText(/no agent with this id/i)).toBeNull();
  });

  it("still warns about a lane that genuinely has no runner", async () => {
    // The half that must survive the fix: recognising built-ins cannot become recognising anything.
    const user = userEvent.setup();
    const field = await lane();
    await user.clear(field);
    await user.type(field, "nao-existe");

    expect(await screen.findByText(/no agent with this id/i)).toBeTruthy();
  });

  it("says nothing while the field is empty", async () => {
    const user = userEvent.setup();
    const field = await lane();
    await user.clear(field);

    expect(screen.queryByText(/no agent with this id/i)).toBeNull();
  });
});
