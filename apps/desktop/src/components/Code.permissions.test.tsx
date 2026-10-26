import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getRuns, listCodeSessions, patchCodeBypass } from "@/lib/api";
import { emptyTree, gitStatus } from "@/test/code-api-mock";
import { scriptTurn } from "@/test/code-api-mock";
import { streamCodeTurn } from "@/lib/api";
import { WORKSPACE_KEY } from "@/lib/workspace";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
vi.mock("@/components/ui/toast", () => ({ useOptionalToast: () => null }));

let bypassEnabled = false;

async function openCode() {
  localStorage.setItem(WORKSPACE_KEY, "/repo");
  localStorage.setItem("chimera:code:last-session:/repo", "s1");
  vi.mocked(getFsTree).mockResolvedValue(emptyTree());
  vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
  vi.mocked(getRuns).mockResolvedValue([]);
  vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
  vi.mocked(listCodeSessions).mockImplementation(async () => [
    { id: "s1", workspace: "/repo", title: "Conversation", turns: 1, updated_at: 1, bypass_permissions: bypassEnabled },
  ]);
  renderWithProviders(<Code />);
  return userEvent.setup();
}

describe("Code — conversation permission bypass", () => {
  beforeEach(() => {
    bypassEnabled = false;
    vi.mocked(patchCodeBypass).mockReset().mockImplementation(async (id, bypass_permissions) => {
      bypassEnabled = bypass_permissions;
      return { id, bypass_permissions };
    });
  });

  it("shows an error after refusal and re-enables the button", async () => {
    vi.mocked(patchCodeBypass).mockRejectedValue(new Error("403 Forbidden"));
    const user = await openCode();
    const button = await screen.findByRole("button", { name: "Ignore permissions" });
    await user.click(button);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not change this conversation’s permission setting. Please try again.",
    );
    await waitFor(() => expect(button).toBeEnabled());
  });

  it("changes the label after the update succeeds", async () => {
    const user = await openCode();
    await user.click(await screen.findByRole("button", { name: "Ignore permissions" }));

    const buttons = await screen.findAllByRole("button", { name: "Stop ignoring permissions" });
    expect(buttons[buttons.length - 1]).toBeEnabled();
    expect(patchCodeBypass).toHaveBeenCalledWith("s1", true);
  });

  it("disables the button while the update is pending", async () => {
    let finish: ((value: { id: string; bypass_permissions: boolean }) => void) | undefined;
    vi.mocked(patchCodeBypass).mockImplementation(
      () => new Promise((resolve) => { finish = resolve; }),
    );
    const user = await openCode();
    const button = await screen.findByRole("button", { name: "Ignore permissions" });
    await user.click(button);
    expect(button).toBeDisabled();
    await act(async () => finish?.({ id: "s1", bypass_permissions: true }));
    expect(patchCodeBypass).toHaveBeenCalledWith("s1", true);
  });
});
