import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RunLauncher } from "@/components/run/RunLauncher";
import { getPausedRuns, getPlan, getRequirements, streamRun } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const ITEMS = [
  { text: "a página mostra o cardápio", kind: "include" },
  { text: "a página diz o horário", kind: "include" },
  { text: "não usar texto de enchimento", kind: "avoid" },
];

/**
 * The requirement checklist, read and corrected before the run.
 *
 * The audit's answer to "how does a layman's prompt become something good" is not a better prompt:
 * it is three texts the person can correct. The plan shipped in wave 1, the Spec in wave 2, and
 * this is the third — the one whose whole value is the editing. Reading "include: a contact form"
 * is how somebody notices they never said "with the menu".
 *
 * The failure these tests exist to prevent is the one this codebase produces repeatedly: the screen
 * offers an edit and the run re-derives the value, so the edit is decoration. Here that would be
 * worse than having no checklist, because a re-extracted list still looks reviewed.
 */
describe("the requirement checklist", () => {
  beforeEach(() => {
    vi.mocked(getPlan).mockReset().mockResolvedValue({ steps: ["1"], text: "1. faça", note: "" } as never);
    vi.mocked(getRequirements).mockReset().mockResolvedValue({ items: ITEMS, note: "" } as never);
    vi.mocked(getPausedRuns).mockResolvedValue([] as never);
    vi.mocked(streamRun).mockReset().mockImplementation(async () => {});
  });

  async function askForThePlan(task = "faça uma página da padaria") {
    renderWithProviders(<RunLauncher />);
    await userEvent.type(screen.getByLabelText(/task/i), task);
    await userEvent.click(screen.getByRole("button", { name: /see the plan/i }));
    await screen.findByText(/has to cover/i);
  }

  it("comes back with the plan, in one sitting", async () => {
    // Together on purpose: it is holding the plan against the checklist that surfaces the thing
    // nobody asked for. Two separate buttons would make the second one optional, and it is the one
    // that carries the acceptance criteria.
    await askForThePlan();

    expect(screen.getByDisplayValue("a página mostra o cardápio")).toBeTruthy();
    expect(getPlan).toHaveBeenCalledOnce();
    expect(getRequirements).toHaveBeenCalledOnce();
  });

  it("labels which kind each line is", async () => {
    // A weak model drops `avoid` and `include` first — "must do X" survives context growth and
    // "don't do Y" quietly does not — so the kind is the most useful thing on the line.
    await askForThePlan();

    expect(screen.getAllByText(/include/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/avoid/i)).toBeTruthy();
  });

  it("sends the list as the person left it", async () => {
    await askForThePlan();

    await userEvent.click(screen.getByLabelText(/remove não usar texto de enchimento/i));
    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    const sent = vi.mocked(streamRun).mock.calls[0][0] as { requirements: { text: string }[] };
    expect(sent.requirements.map((r) => r.text)).toEqual([
      "a página mostra o cardápio",
      "a página diz o horário",
    ]);
  });

  it("sends an edit, not the extracted text", async () => {
    await askForThePlan();

    const field = screen.getByDisplayValue("a página diz o horário");
    await userEvent.clear(field);
    await userEvent.type(field, "diz que abrimos 7h");
    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    const sent = vi.mocked(streamRun).mock.calls[0][0] as { requirements: { text: string }[] };
    expect(sent.requirements.map((r) => r.text)).toContain("diz que abrimos 7h");
  });

  it("lets somebody add the thing they forgot to ask for", async () => {
    // The reason the panel exists at all. Whatever gets added here becomes an acceptance criterion
    // for free — the same list is the AND-gate at the end of the run.
    await askForThePlan();

    await userEvent.click(screen.getByRole("button", { name: /add a line/i }));
    // Targeted by the line's own stable label rather than "whichever field is empty" — the form
    // has other empty fields, and a test that picks one by emptiness is measuring the form layout.
    await userEvent.type(screen.getByLabelText(/requirement 4/i), "mostra o telefone");
    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    const sent = vi.mocked(streamRun).mock.calls[0][0] as { requirements: { text: string }[] };
    expect(sent.requirements.map((r) => r.text)).toContain("mostra o telefone");
  });

  it("sends null when nobody was ever asked", async () => {
    // The control, and it carries the ethics. A run that arms an acceptance gate on a list its
    // owner never saw is the same failure this feature exists to fix, wearing the opposite sign.
    renderWithProviders(<RunLauncher />);
    await userEvent.type(screen.getByLabelText(/task/i), "faça x");
    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect((vi.mocked(streamRun).mock.calls[0][0] as { requirements: unknown }).requirements).toBeNull();
  });

  it("discarding the plan discards the checklist with it", async () => {
    // Half a review is worse than none: a run carrying an approved checklist next to a discarded
    // plan is carrying the half nobody meant to keep.
    await askForThePlan();

    await userEvent.click(screen.getByRole("button", { name: /discard/i }));
    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));

    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect((vi.mocked(streamRun).mock.calls[0][0] as { requirements: unknown }).requirements).toBeNull();
  });

  it("survives a requirements call that resolves with nothing", async () => {
    // Found by CI, not locally: `Promise.allSettled` reports a resolved-with-nothing call as
    // `fulfilled`, so reading through `.value` threw inside the handler — past any catch — and
    // left the panel open, empty and silent. A local `npm test` did not fail on the unhandled
    // rejection and CI did, which is precisely why this is pinned rather than trusted to types.
    vi.mocked(getRequirements).mockResolvedValue(undefined as never);
    renderWithProviders(<RunLauncher />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /see the plan/i }));

    // The plan still arrives — one half falling over must not take the other down.
    expect(await screen.findByDisplayValue(/1\. faça/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));
    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    expect((vi.mocked(streamRun).mock.calls[0][0] as { requirements: unknown }).requirements).toBeNull();
  });

  it("says nothing was read rather than showing an empty list", async () => {
    // An empty checklist reads as "this task has no requirements", which is a claim nobody made.
    vi.mocked(getRequirements).mockResolvedValue({ items: [], note: "" } as never);
    renderWithProviders(<RunLauncher />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /see the plan/i }));

    expect(await screen.findByText(/could be read/i)).toBeTruthy();
  });
});
