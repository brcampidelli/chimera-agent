import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Tasks } from "@/components/Tasks";
import { getKanban, getProjects, streamKanbanRun } from "@/lib/api";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  addKanbanCard: vi.fn(),
  approveProject: vi.fn(),
  denyProject: vi.fn(),
  getAgentRegistry: vi.fn(async () => []),
  getKanban: vi.fn(),
  getProject: vi.fn(),
  getProjects: vi.fn(async () => []),
  moveKanbanCard: vi.fn(),
  removeKanbanCard: vi.fn(),
  startProject: vi.fn(),
  stepProject: vi.fn(),
  streamKanbanRun: vi.fn(),
}));

/**
 * Where the board works its cards.
 *
 * It used to send nothing, so the backend fell back to its own launch directory. For an installed
 * desktop build that is the folder the app was installed into — measured on a real install: the
 * agent's root became `%LOCALAPPDATA%\Chimera`, holding 4757 files and the app's own `.env`, and
 * `read_file(".env")` returned the API key in full. A card naming a file in the user's project
 * could not find it, and the run had reach nobody asked for.
 *
 * The Code screen picks the project once; every surface reads that same choice.
 */
describe("the board works cards in the chosen project", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.mocked(getKanban).mockReset().mockResolvedValue({ backlog: [], doing: [], review: [], done: [], blocked: [] });
    vi.mocked(getProjects).mockReset().mockResolvedValue([]);
    vi.mocked(streamKanbanRun).mockReset().mockImplementation(async (_req, h) => {
      h.onDone?.({ worked: 0 });
    });
  });

  it("sends the project the user chose", async () => {
    localStorage.setItem(WORKSPACE_KEY, "/projects/cafe-aurora");
    renderWithProviders(<Tasks />);

    await userEvent.click(await screen.findByRole("button", { name: /Run the board/ }));

    const enviado = vi.mocked(streamKanbanRun).mock.calls[0]?.[0];
    expect(enviado?.workspace).toBe("/projects/cafe-aurora");
  });

  it("sends null rather than an empty string when no project is chosen", async () => {
    // The control, and it is not pedantry: `""` is falsy on the server too, but it round-trips as
    // "the user chose emptiness" rather than "the user has not chosen" — the same distinction
    // `writeWorkspace` makes when it clears the key instead of storing "".
    renderWithProviders(<Tasks />);

    await userEvent.click(await screen.findByRole("button", { name: /Run the board/ }));

    const enviado = vi.mocked(streamKanbanRun).mock.calls[0]?.[0];
    expect(enviado?.workspace).toBeNull();
  });
});
