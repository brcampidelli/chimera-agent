import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * This suite used to exercise a three-way profile selector and the table under it: which model each
 * role resolves to, where a fusion panel is marked, that Verify has no model, and the standing
 * "routing is not measured yet" note.
 *
 * The selector is gone — with one model configured it showed the same slug four times, and with
 * several it asked the user to choose between options nobody has evidence about. What it protected
 * did not go with it, it moved:
 *
 * - The routing RULES (fusion only on the tool-free turns; Verify has no model; the reviewer refuses
 *   to be the model that wrote the patch) are asserted in `tests/test_roles.py`, against the
 *   resolver itself rather than against a table rendering it.
 * - The "not measured" claim now lives with the benchmark it points at, `bench/role_routing`, and no
 *   longer needs a disclaimer beside a control the user cannot press.
 * - What CANNOT move is below: the app must still send a profile, and must say that IT chose.
 */
describe("Code — the profile the system applies", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("still sends a profile rather than letting the server default it", async () => {
    // Omitting `profile` is not "apply the default". It means no cheap explorer, no fusion on the
    // deliberative turns, and — the expensive one — a Manager reviewing with the very model that
    // wrote the patch, which is the self-grading collapse the reviewer rule exists to prevent.
    const user = userEvent.setup();
    renderWithProviders(<Code />);

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "what does this do?");
    await user.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].profile).toBe("balanced");
  });

  it("offers no profile control at all", async () => {
    // The regression this guards is a selector creeping back in "just to expose the option": three
    // choices with no measured difference between them is a question the user cannot answer, and
    // the app asking it implies an answer exists.
    renderWithProviders(<Code />);
    // Wait on the composer, not on the posture call — the posture query is skipped entirely until a
    // project is chosen, so it stopped being a "the screen has settled" signal.
    await screen.findByPlaceholderText(/^Ask about this code/);

    expect(screen.queryByRole("button", { name: /^economy$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^max$/i })).not.toBeInTheDocument();
  });
});
