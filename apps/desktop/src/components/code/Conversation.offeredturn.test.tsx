import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { streamCodeTurn } from "@/lib/api";
import { offerTurn, takeOfferedTurn } from "@/lib/offered-turn";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A turn offered by another screen (the pull request card's "fix the CI", C1) lands in the
 * conversation's message box and is NOT sent. Sending stays the owner's press, so the turn is an
 * ordinary one under the ordinary governance; an app that sent it on arrival would be running a turn
 * nobody pressed.
 */
function mount(sessionId: string) {
  renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={false}
      controls={null}
      onOpenFile={() => {}}
      resumeSession={sessionId}
    />,
  );
}

describe("a turn offered to a conversation", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    localStorage.clear();
  });

  it("fills the draft once and sends nothing", async () => {
    offerTurn("s9", "The CI of pull request #42 failed in: `lint`.");
    mount("s9");

    const box = await screen.findByRole("textbox");
    await waitFor(() => expect(box).toHaveValue("The CI of pull request #42 failed in: `lint`."));
    // Taken: reopening the conversation does not offer it a second time.
    expect(takeOfferedTurn("s9")).toBeNull();
    expect(streamCodeTurn).not.toHaveBeenCalled();
  });

  it("is only for the conversation it was offered to", async () => {
    offerTurn("other", "not for this one");
    mount("s9");

    const box = await screen.findByRole("textbox");
    expect(box).toHaveValue("");
    expect(takeOfferedTurn("other")).toBe("not for this one");
  });
});
