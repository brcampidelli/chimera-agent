import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TaskConsole } from "@/components/work/TaskConsole";
import { getPausedRuns, getPlan, getRequirements, streamRun } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const ITEMS = [{ text: "a página mostra o cardápio", kind: "include" }];

/**
 * The run seams the CLI has always had and no screen ever sent.
 *
 * `repo_map`, `explorer`, `replan` and spec-grounded test generation were all built, tested and
 * reachable only from a terminal — `repo_map` and `explorer` were even declared on the request type
 * and defaulted to false with nothing on earth setting them.
 *
 * One control per idea, not one per flag: choosing between a repository map and an explorer tool is
 * not a choice anybody has. And the test-generation control appears only where it changes the
 * verdict — a control that does nothing teaches people not to read the ones that do.
 */
describe("the run seams", () => {
  beforeEach(() => {
    vi.mocked(getPlan).mockReset().mockResolvedValue({ steps: ["1"], text: "1. faça", note: "" } as never);
    vi.mocked(getRequirements).mockReset().mockResolvedValue({ items: ITEMS, note: "" } as never);
    vi.mocked(getPausedRuns).mockResolvedValue([] as never);
    vi.mocked(streamRun).mockReset().mockImplementation(async () => {});
  });

  async function run(): Promise<Record<string, unknown>> {
    await userEvent.click(screen.getByRole("button", { name: /^run$/i }));
    await waitFor(() => expect(streamRun).toHaveBeenCalled());
    return vi.mocked(streamRun).mock.calls[0][0] as unknown as Record<string, unknown>;
  }

  it("sends both halves of knowing the repository from one control", async () => {
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "conserte o login");
    await userEvent.click(screen.getByLabelText(/way around this project/i));

    const sent = await run();
    expect(sent.repo_map).toBe(true);
    expect(sent.explorer).toBe(true);
  });

  it("leaves them off when nobody asked", async () => {
    // The control. These are not free — a repository map is a digest the planner pays for, and an
    // explorer is a tool the worker can spend steps in.
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");

    const sent = await run();
    expect(sent.repo_map).toBe(false);
    expect(sent.explorer).toBe(false);
  });

  it("sends replan when asked", async () => {
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");
    await userEvent.click(screen.getByLabelText(/rethink instead of retrying/i));

    expect((await run()).replan).toBe(true);
  });

  it("offers test generation only once a checklist has been reviewed", async () => {
    // There is nothing to ground the generation in before that, and the loop would ignore it.
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");

    expect(screen.queryByLabelText(/real tests/i)).toBeNull();
  });

  it("does not offer it when a test command is already typed", async () => {
    // With a real command the tests ARE the ground truth. Offering to generate more would be
    // offering to replace something strong with something weaker.
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");
    await userEvent.type(screen.getByLabelText(/the check/i), "pytest -q");
    await userEvent.click(screen.getByRole("button", { name: /see the plan/i }));
    await screen.findByText(/has to cover/i);

    expect(screen.queryByLabelText(/real tests/i)).toBeNull();
  });

  it("sends it when there is a checklist and no test command", async () => {
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /see the plan/i }));
    await screen.findByText(/has to cover/i);
    await userEvent.click(screen.getByLabelText(/real tests/i));

    expect((await run()).gen_tests).toBe(true);
  });

  it("says it writes a file, because it does", async () => {
    // Writing into somebody's project is a side effect, and a checkbox that causes one without
    // saying so is the app doing something its owner did not agree to.
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /see the plan/i }));
    await screen.findByText(/has to cover/i);

    expect(screen.getByText(/writes a test file/i)).toBeTruthy();
  });

  it("withdraws the request when the checklist is emptied after being ticked", async () => {
    // The tick can outlive the thing it was about: delete every line and the loop would ignore
    // `gen_tests` anyway, so sending true would be the screen claiming a gate that will not run.
    renderWithProviders(<TaskConsole workspace="" onOpenCode={() => {}} />);
    await userEvent.type(screen.getByLabelText(/task/i), "x");
    await userEvent.click(screen.getByRole("button", { name: /see the plan/i }));
    await screen.findByText(/has to cover/i);
    await userEvent.click(screen.getByLabelText(/real tests/i));
    await userEvent.click(screen.getByLabelText(/remove a página mostra o cardápio/i));

    expect((await run()).gen_tests).toBe(false);
  });
});
