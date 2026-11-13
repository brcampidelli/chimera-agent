import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TaskSuggestionCards } from "@/components/code/TaskSuggestionCards";
import { I18nProvider } from "@/lib/i18n";
import type { TaskCardState } from "@/lib/taskSuggestions";

const card = (id: string, extra: Partial<TaskCardState> = {}): TaskCardState => ({
  id,
  title: `Task ${id}`,
  tldr: `Why ${id} matters`,
  prompt: `Do ${id} in a new conversation: read chimera/x.py, remove parse_legacy, run the suite.`,
  tainted: false,
  ...extra,
});

function mount(cards: TaskCardState[], busy = false) {
  const handlers = {
    onStart: vi.fn(),
    onStartAll: vi.fn(),
    onHere: vi.fn(),
    onDismiss: vi.fn(),
  };
  render(
    <I18nProvider>
      <TaskSuggestionCards cards={cards} busy={busy} {...handlers} />
    </I18nProvider>,
  );
  return handlers;
}

describe("TaskSuggestionCards", () => {
  it("says it is fetching the remote base while a new worktree is being made, and only then", () => {
    mount([card("a", { status: "starting", fetchingBase: true }), card("b", { status: "starting" })]);
    const [first, second] = screen.getAllByTestId("task-suggestion-card");
    expect(within(first).getByRole("status").textContent).toBe("Fetching the remote base…");
    expect(within(second).queryByRole("status")).toBeNull();
  });

  it("puts the server's warning about the base in evidence once the task started", () => {
    mount([
      card("a", {
        status: "started",
        baseWarning: "Local HEAD abc is behind origin/main at def; the new worktree starts from origin/main.",
      }),
    ]);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("did not start from the remote base");
    expect(alert.textContent).toContain("origin/main at def");
  });

  it("renders nothing when the turn proposed no card", () => {
    mount([]);
    expect(screen.queryByTestId("task-suggestion-cards")).toBeNull();
  });

  it("shows the title, the reason and the owner actions for each card", () => {
    mount([card("a")]);
    const article = screen.getByTestId("task-suggestion-card");
    expect(within(article).getByText("Task a")).toBeTruthy();
    expect(within(article).getByText("Why a matters")).toBeTruthy();
    for (const label of ["Start", "Do it here", "Options", "Dismiss"]) {
      expect(within(article).getByRole("button", { name: label })).toBeTruthy();
    }
  });

  it("shows the full prompt that will run, before the owner starts it", async () => {
    const user = userEvent.setup();
    const handlers = mount([card("a")]);
    const toggle = screen.getByRole("button", { name: "Show the full prompt" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await user.click(toggle);
    const shown = screen.getByTestId("task-suggestion-prompt");
    expect(shown.textContent).toBe("Do a in a new conversation: read chimera/x.py, remove parse_legacy, run the suite.");
    expect(handlers.onStart).not.toHaveBeenCalled();
    expect(handlers.onHere).not.toHaveBeenCalled();
  });

  it("warns, on the card, when the proposal came from a turn that read external content", () => {
    mount([card("a", { tainted: true }), card("b")]);
    const [tainted, clean] = screen.getAllByTestId("task-suggestion-card");
    expect(within(tainted).getByRole("note").textContent).toContain("read external content");
    expect(within(clean).queryByRole("note")).toBeNull();
  });

  it("starts only on the owner's click, with the options as they were set", async () => {
    const user = userEvent.setup();
    const handlers = mount([card("a")]);
    expect(handlers.onStart).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Options" }));
    await user.click(screen.getByRole("checkbox"));
    await user.type(screen.getByPlaceholderText("Project default"), "opus");
    await user.click(screen.getByRole("button", { name: "Start" }));
    expect(handlers.onStart).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }), {
      model: "opus",
      newWorktree: true,
    });
  });

  it("states that the new conversation uses the project's posture and bypass, never the old wording", async () => {
    const user = userEvent.setup();
    mount([card("a")]);
    await user.click(screen.getByRole("button", { name: "Options" }));
    const panel = screen.getByTestId("task-suggestion-options");
    expect(panel.textContent).toContain("uses the project's posture and bypass");
    expect(panel.textContent).not.toContain("never copied");
  });

  it("disables Do it here while a turn runs, and says why", async () => {
    const user = userEvent.setup();
    const handlers = mount([card("a")], true);
    const here = screen.getByRole("button", { name: "Do it here" }) as HTMLButtonElement;
    expect(here.disabled).toBe(true);
    expect(screen.getByText("Wait for the current turn to finish before sending this here.")).toBeTruthy();
    await user.click(here);
    expect(handlers.onHere).not.toHaveBeenCalled();
  });

  it("sends the prompt into the current conversation for Do it here when no turn runs", async () => {
    const user = userEvent.setup();
    const handlers = mount([card("a")]);
    await user.click(screen.getByRole("button", { name: "Do it here" }));
    expect(handlers.onHere).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }));
  });

  it("asks for confirmation with the count before Start all, and starts nothing until it is confirmed", async () => {
    const user = userEvent.setup();
    const handlers = mount([card("a"), card("b"), card("c")]);
    await user.click(screen.getByRole("button", { name: "Start all" }));
    expect(handlers.onStartAll).not.toHaveBeenCalled();
    expect(screen.getByText(/Start 3 conversations\?/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(handlers.onStartAll).toHaveBeenCalledTimes(1);
    expect(handlers.onStartAll.mock.calls[0][0].map((c: TaskCardState) => c.id)).toEqual(["a", "b", "c"]);
  });

  it("caps one Start all click at five cards and says so in the confirmation", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 7 }, (_, i) => card(`k${i}`));
    const handlers = mount(many);
    await user.click(screen.getByRole("button", { name: "Start all" }));
    expect(screen.getByText(/Start 5 conversations\?.*At most 5 per click/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(handlers.onStartAll.mock.calls[0][0]).toHaveLength(5);
  });

  it("can cancel Start all without starting anything", async () => {
    const user = userEvent.setup();
    const handlers = mount([card("a"), card("b")]);
    await user.click(screen.getByRole("button", { name: "Start all" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(handlers.onStartAll).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("offers Start all only when more than one card is still waiting", () => {
    mount([card("a"), card("b", { status: "started", startedSessionId: "s2" })]);
    expect(screen.queryByRole("button", { name: "Start all" })).toBeNull();
    expect(screen.getByText("Started in a new conversation")).toBeTruthy();
    cleanup();
  });

  it("shows a failed start with its reason and keeps the card startable", () => {
    mount([card("a", { status: "failed", error: "the project is not registered" })]);
    expect(screen.getByRole("alert").textContent).toContain("the project is not registered");
    expect(screen.getByRole("button", { name: "Start" })).toBeTruthy();
  });
});
