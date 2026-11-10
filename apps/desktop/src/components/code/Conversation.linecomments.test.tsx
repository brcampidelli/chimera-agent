import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { streamCodeTurn, uploadAttachment } from "@/lib/api";
import { requestSendLineComments, saveLineComment } from "@/lib/line-comments";
import { scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const IMAGE = { id: "a1", name: "screen.png", kind: "image", chars: 0, note: "" };

function mount(sessionId: string, busyElsewhere = false) {
  renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={busyElsewhere}
      controls={null}
      onOpenFile={() => {}}
      resumeSession={sessionId}
    />,
  );
}

/** A comment on the Changes card's diff of `src/a.ts`, waiting for the conversation to send it. */
function pendingComment(sessionId: string) {
  saveLineComment(
    sessionId,
    { origin: "git", file: "src/a.ts", side: "new", line: 1, marker: "+", content: "const x = 1;" },
    "rename this",
  );
}

/** Attaches the image through the composer's own paperclip input, as the owner would. */
async function attachImage(user: ReturnType<typeof userEvent.setup>) {
  vi.mocked(uploadAttachment).mockResolvedValue(IMAGE);
  const input = await screen.findByLabelText("Attach");
  await user.upload(input as HTMLInputElement, new File(["x"], IMAGE.name));
  await screen.findByText("screen.png");
}

describe("sending the diff comments from a conversation", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockImplementation(scriptTurn());
    vi.mocked(uploadAttachment).mockReset();
    localStorage.clear();
  });

  it("sends the comments and keeps the draft and the attachments the owner is writing", async () => {
    const user = userEvent.setup();
    pendingComment("s-keep");
    mount("s-keep");
    await attachImage(user);
    await user.type(screen.getByRole("textbox"), "meu rascunho");

    await user.click(await screen.findByRole("button", { name: "Send comments (1)" }));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    const [request] = vi.mocked(streamCodeTurn).mock.calls[0];
    expect(request.message).toContain("Comment: rename this");
    expect(request.message).not.toContain("meu rascunho");
    expect(request.attachments ?? []).toEqual([]);

    expect(screen.getByRole("textbox")).toHaveValue("meu rascunho");
    expect(screen.getByText("screen.png")).toBeInTheDocument();
    // Accepted: the list is cleared.
    expect(screen.queryByRole("button", { name: /Send comments/ })).not.toBeInTheDocument();
  });

  it("still clears the composer, its draft and attachments, on an ordinary send", async () => {
    const user = userEvent.setup();
    mount("s-normal");
    await attachImage(user);
    await user.type(screen.getByRole("textbox"), "uma pergunta{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    const [request] = vi.mocked(streamCodeTurn).mock.calls[0];
    expect(request.message).toBe("uma pergunta");
    expect(request.attachments).toEqual(["a1"]);

    expect(screen.getByRole("textbox")).toHaveValue("");
    expect(screen.queryByText("screen.png")).not.toBeInTheDocument();
  });

  it("keeps the comments when a turn is running elsewhere, and says why", async () => {
    pendingComment("s-busy");
    mount("s-busy", true);

    act(() => requestSendLineComments("s-busy"));

    expect(streamCodeTurn).not.toHaveBeenCalled();
    expect(await screen.findByText(/Wait for the turn/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Send comments (1)" })).toBeDisabled();
  });
});
