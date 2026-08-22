import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns } from "@/lib/api";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const OPEN_PROJECT = "/home/me/the-project";

/**
 * Choosing a folder is a NAVIGATION gesture. It must not decide anything.
 *
 * The picker's button lives inside the form that opens a typed path, and a `<button>` in a form
 * submits it unless it says otherwise — so the click ran the submit handler as well as the toggle,
 * and the submit handler is `switchProject`, which persists a new root and starts a new
 * conversation. The natural version of the gesture is the damaging one: you click Browse BECAUSE
 * you do not want to type, so the field is empty, so the project you had open is switched to
 * nothing before the picker has shown you a single folder to choose from.
 */
describe("Code — choosing a folder does not decide one", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(getRuns).mockResolvedValue([]);
    localStorage.setItem(WORKSPACE_KEY, OPEN_PROJECT);
  });

  it("keeps the open project when the field is empty", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    // The field is CLEARED first, and that is the whole test rather than setup. It arrives
    // pre-filled with the open project, so leaving it alone makes the submit a no-op via
    // `switchProject`'s `next === workspace` guard — and the assertion below then passes against
    // the broken build too. Emptying it is also the honest reproduction: you click Browse
    // precisely because you have nothing to type.
    await user.clear(await screen.findByPlaceholderText(/folder path/i));
    await user.click(screen.getByRole("button", { name: /Choose folder/ }));

    expect(localStorage.getItem(WORKSPACE_KEY)).toBe(OPEN_PROJECT);
  });

  it("keeps the open project when a path is typed but not opened", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    // Typing a path is not opening it. Someone reaching for the picker with half a path in the
    // field is the most likely person to click Browse, and the submit would have taken the half.
    // The field starts pre-filled with the open project, so it is cleared first — otherwise the
    // typing appends and the test asserts about a path nobody would ever have entered.
    const field = await screen.findByPlaceholderText(/folder path/i);
    await user.clear(field);
    await user.type(field, "/home/me/some-other");
    await user.click(screen.getByRole("button", { name: /Choose folder/ }));

    expect(localStorage.getItem(WORKSPACE_KEY)).toBe(OPEN_PROJECT);
  });

  it("still opens the typed path when Open is the button that was pressed", async () => {
    // The guard above must not be a paralysed form: the deliberate gesture has to keep working,
    // or this test file would pass just as well against a form that opens nothing at all.
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    const field = await screen.findByPlaceholderText(/folder path/i);
    await user.clear(field);
    await user.type(field, "/home/me/chosen");
    await user.click(screen.getByRole("button", { name: "Open" }));

    expect(localStorage.getItem(WORKSPACE_KEY)).toBe("/home/me/chosen");
  });
});
