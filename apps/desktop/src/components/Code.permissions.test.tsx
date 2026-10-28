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

async function openCode({ listed = true }: { listed?: boolean } = {}) {
  localStorage.setItem(WORKSPACE_KEY, "/repo");
  localStorage.setItem("chimera:code:last-session:/repo", "s1");
  vi.mocked(getFsTree).mockResolvedValue(emptyTree());
  vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
  vi.mocked(getRuns).mockResolvedValue([]);
  vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
  vi.mocked(listCodeSessions).mockImplementation(async () => (listed ? [
    { id: "s1", workspace: "/repo", title: "Conversation", turns: 1, updated_at: 1, bypass_permissions: bypassEnabled },
  ] : []));
  renderWithProviders(<Code />);
  return userEvent.setup();
}

describe("Code — conversation permission bypass", () => {
  beforeEach(() => {
    bypassEnabled = false;
    vi.mocked(patchCodeBypass).mockReset().mockImplementation(async (id, bypass_permissions) => {
      bypassEnabled = bypass_permissions;
      return { id, bypass_permissions, approved_waiting: 0, left_waiting: 0 };
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
    let finish: ((value: { id: string; bypass_permissions: boolean; approved_waiting: number; left_waiting: number }) => void) | undefined;
    vi.mocked(patchCodeBypass).mockImplementation(
      () => new Promise((resolve) => { finish = resolve; }),
    );
    const user = await openCode();
    const button = await screen.findByRole("button", { name: "Ignore permissions" });
    await user.click(button);
    expect(button).toBeDisabled();
    await act(async () => finish?.({ id: "s1", bypass_permissions: true, approved_waiting: 0, left_waiting: 0 }));
    expect(patchCodeBypass).toHaveBeenCalledWith("s1", true);
  });

  it("switches the label for a running conversation that is not in the stored list", async () => {
    // The first turn is still running, so its file does not exist yet and the list does not carry
    // it with a flag. The switch must still read the server's answer, not the list's absence of one.
    const user = await openCode({ listed: false });
    await user.click(await screen.findByRole("button", { name: "Ignore permissions" }));

    expect(patchCodeBypass).toHaveBeenCalledWith("s1", true);
    const buttons = await screen.findAllByRole("button", { name: "Stop ignoring permissions" });
    expect(buttons[buttons.length - 1]).toBeEnabled();
  });

  it("does not send the same value again when the label already shows it", async () => {
    const user = await openCode({ listed: false });
    await user.click(await screen.findByRole("button", { name: "Ignore permissions" }));
    const buttons = await screen.findAllByRole("button", { name: "Stop ignoring permissions" });
    await user.click(buttons[buttons.length - 1]);

    expect(vi.mocked(patchCodeBypass).mock.calls.map((call) => call[1])).toEqual([true, false]);
  });
});
