import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskConsole } from "@/components/work/TaskConsole";
import { getPausedRuns, getPlan, streamRun } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * Reading what the agent intends to do, before it does any of it.
 *
 * `POST /api/plan` runs only the planner — one tool-free model call, no edits, nothing touching the
 * workspace — and `RunRequest.plan` executes an approved plan verbatim instead of planning again.
 * Both have been on the server since runs existed. Nothing called either: the TypeScript helper was
 * deleted for having no caller, and a test in this repo asserted its absence.
 *
 * It is the one moment in a run where a correction costs nothing. Afterwards, every correction is a
 * revert.
 */
describe("the plan gate", () => {
  beforeEach(() => {
    vi.mocked(getPausedRuns).mockResolvedValue([]);
    vi.mocked(getPlan).mockReset().mockResolvedValue({
      steps: ["Read index.html", "Add the viewport meta"],
      text: "1. Read index.html\n2. Add the viewport meta",
      note: "",
    });
    vi.mocked(streamRun).mockReset();
  });

  async function askFor(task: string) {
    const user = userEvent.setup();
    renderWithProviders(<TaskConsole workspace="/proj" onOpenCode={() => {}} />);
    await user.type(screen.getByLabelText(/the task/i), task);
    return user;
  }

  it("shows the plan without starting anything", async () => {
    // The whole promise of the cheap button: one model call, and the workspace is untouched.
    const user = await askFor("adiciona o meta viewport");

    await user.click(screen.getByRole("button", { name: /see the plan/i }));

    await waitFor(() => expect(getPlan).toHaveBeenCalled());
    expect(streamRun).not.toHaveBeenCalled();
    expect(await screen.findByDisplayValue(/Add the viewport meta/)).toBeTruthy();
  });

  it("sends the plan the user edited, not the one the planner wrote", async () => {
    // Editable is the entire point. A plan you can only approve is a plan you can only agree with,
    // and the person reading it is the one who knows what was left out.
    const user = await askFor("adiciona o meta viewport");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));

    const box = await screen.findByLabelText(/^the plan$/i);
    await user.clear(box);
    await user.type(box, "1. Só mexer no index.html");
    await user.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    const enviado = vi.mocked(streamRun).mock.calls[0][0] as { plan?: string | null };
    expect(enviado.plan).toContain("Só mexer no index.html");
  });

  it("sends no plan when nobody asked to see one", async () => {
    // The control, and the compatibility promise: a run started the old way must be the old run.
    // `RunRequest.plan` is null-checked on the server, and a plan of "" is not the absence of one.
    const user = await askFor("adiciona o meta viewport");

    await user.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect((vi.mocked(streamRun).mock.calls[0][0] as { plan?: string | null }).plan).toBeNull();
  });

  it("discarding the plan goes back to planning for itself", async () => {
    const user = await askFor("adiciona o meta viewport");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));
    await screen.findByLabelText(/^the plan$/i);

    await user.click(screen.getByRole("button", { name: /discard/i }));
    await user.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect((vi.mocked(streamRun).mock.calls[0][0] as { plan?: string | null }).plan).toBeNull();
  });

  it("says so when the planner comes back empty", async () => {
    // The endpoint degrades to an empty plan with a note rather than failing, so an empty answer
    // needs a sentence: a blank box reads as "it plans to do nothing".
    vi.mocked(getPlan).mockResolvedValue({ steps: [], text: "", note: "" });
    const user = await askFor("???");

    await user.click(screen.getByRole("button", { name: /see the plan/i }));

    expect(await screen.findByText(/came back with nothing/i)).toBeTruthy();
  });

  it("a failed plan call does not block the run", async () => {
    // Planning is an offer, not a gate. A provider hiccup while previewing must not take away the
    // ability to just run the thing.
    vi.mocked(getPlan).mockRejectedValue(new Error("boom"));
    const user = await askFor("adiciona o meta viewport");

    await user.click(screen.getByRole("button", { name: /see the plan/i }));
    expect(await screen.findByText(/could not get a plan/i)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: /^run$/i }));
    await waitFor(() => expect(streamRun).toHaveBeenCalled());
  });
});

describe("the plan panel stays put while you edit it", () => {
  beforeEach(() => {
    vi.mocked(getPausedRuns).mockResolvedValue([]);
    vi.mocked(getPlan).mockReset().mockResolvedValue({
      steps: ["Read index.html"],
      text: "1. Read index.html",
      note: "",
    });
    vi.mocked(streamRun).mockReset();
  });

  it("survives being emptied, because rewriting starts with emptying", async () => {
    // Found by a test, not by reasoning: the panel used to render on `plan || planNote`, so
    // clearing the box to rewrite the plan removed the box. The person is left staring at the
    // place their plan used to be, with no way back except asking for a new one.
    const user = userEvent.setup();
    renderWithProviders(<TaskConsole workspace="/proj" onOpenCode={() => {}} />);
    await user.type(screen.getByLabelText(/the task/i), "adiciona o meta viewport");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));

    const box = await screen.findByLabelText(/^the plan$/i);
    await user.clear(box);

    expect(screen.getByLabelText(/^the plan$/i)).toBeTruthy();
  });

  it("closes only when the person says so", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TaskConsole workspace="/proj" onOpenCode={() => {}} />);
    await user.type(screen.getByLabelText(/the task/i), "adiciona o meta viewport");
    await user.click(screen.getByRole("button", { name: /see the plan/i }));
    await screen.findByLabelText(/^the plan$/i);

    await user.click(screen.getByRole("button", { name: /discard/i }));

    expect(screen.queryByLabelText(/^the plan$/i)).toBeNull();
  });
});
