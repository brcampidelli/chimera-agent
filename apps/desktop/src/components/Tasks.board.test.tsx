import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Tasks } from "@/components/Tasks";
import {
  addKanbanCard,
  getKanban,
  getProjects,
  moveKanbanCard,
  removeKanbanCard,
  streamKanbanRun,
} from "@/lib/api";
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
  streamKanbanRun: vi.fn(),
}));

function card(over: Record<string, unknown> = {}) {
  return {
    id: "c1",
    title: "review the parser",
    action: "review the parser",
    column: "backlog",
    success: null,
    risk: null,
    depends_on: [],
    lane: "reviewer",
    verify: null,
    result: "",
    ...over,
  };
}

/**
 * `GET /api/kanban` was the only route the board had, so this screen could render the work and
 * change none of it: filing, moving, removing and dispatching a card were terminal-only.
 */
describe("Tasks — the board stops being a display case", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getProjects).mockResolvedValue([] as never);
    vi.mocked(getKanban).mockResolvedValue({ backlog: [card()] } as never);
    vi.mocked(addKanbanCard).mockResolvedValue(card({ id: "c2" }) as never);
    vi.mocked(moveKanbanCard).mockResolvedValue(card({ column: "doing" }) as never);
    vi.mocked(removeKanbanCard).mockResolvedValue({ deleted: true } as never);
  });

  it("files a card and says who works it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Tasks />);

    await user.type(await screen.findByLabelText("What needs doing?"), "ship the release");
    const lane = screen.getByLabelText("Who works it");
    await user.clear(lane);
    await user.type(lane, "reviewer");
    await user.click(screen.getByRole("button", { name: /^Add$/ }));

    await waitFor(() => expect(addKanbanCard).toHaveBeenCalledOnce());
    expect(vi.mocked(addKanbanCard).mock.calls[0][0]).toEqual({
      title: "ship the release",
      lane: "reviewer",
    });
  });

  it("shows which agent a card belongs to", async () => {
    // A board where every card looks the same cannot answer the question a board is asked.
    renderWithProviders(<Tasks />);
    expect(await screen.findByText("reviewer")).toBeInTheDocument();
  });

  it("moves a card between columns", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Tasks />);

    await user.selectOptions(
      await screen.findByLabelText("Move review the parser"),
      "doing",
    );
    await waitFor(() => expect(moveKanbanCard).toHaveBeenCalledOnce());
    expect(vi.mocked(moveKanbanCard).mock.calls[0]).toEqual(["c1", "doing"]);
  });

  it("removes a card", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Tasks />);

    await user.click(await screen.findByLabelText("Remove review the parser"));
    // react-query hands the mutation fn a second (context) argument, so an exact arg-list
    // match fails on a call that is otherwise right.
    await waitFor(() => expect(removeKanbanCard).toHaveBeenCalledOnce());
    expect(vi.mocked(removeKanbanCard).mock.calls[0][0]).toBe("c1");
  });

  it("reports each card as it lands, not once at the end", async () => {
    // The whole reason the endpoint streams: a dispatch calls models for as long as it has cards.
    const user = userEvent.setup();
    vi.mocked(streamKanbanRun).mockImplementation(async (_req, h) => {
      h.onCard?.({ card_id: "c1", lane: "reviewer", success: true, moved_to: "done" });
      h.onDone?.({ worked: 1 });
    });
    renderWithProviders(<Tasks />);

    await user.click(await screen.findByRole("button", { name: /Run the board/ }));
    expect(await screen.findByText("1 worked")).toBeInTheDocument();
    // The board is refetched as cards land, so a column emptying is visible while it happens.
    await waitFor(() => expect(vi.mocked(getKanban).mock.calls.length).toBeGreaterThan(1));
  });

  it("says zero worked rather than pretending a card ran", async () => {
    // A card whose lane has no runner waits in the backlog. Reporting the queued count instead of
    // the worked one is how someone would never learn their agent is not registered.
    const user = userEvent.setup();
    vi.mocked(streamKanbanRun).mockImplementation(async (_req, h) => {
      h.onDone?.({ worked: 0 });
    });
    renderWithProviders(<Tasks />);

    await user.click(await screen.findByRole("button", { name: /Run the board/ }));
    expect(await screen.findByText("0 worked")).toBeInTheDocument();
  });

  it("can be stopped while it runs", async () => {
    const user = userEvent.setup();
    let aborted = false;
    vi.mocked(streamKanbanRun).mockImplementation(async (_req, _h, signal) => {
      signal?.addEventListener("abort", () => {
        aborted = true;
      });
      await new Promise(() => {}); // never resolves: this is what "still running" looks like
    });
    renderWithProviders(<Tasks />);

    await user.click(await screen.findByRole("button", { name: /Run the board/ }));
    await user.click(await screen.findByRole("button", { name: /Stop/ }));

    expect(aborted).toBe(true);
    expect(screen.getByRole("button", { name: /Run the board/ })).toBeInTheDocument();
  });
});
