import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import {
  getFsTree,
  getGitStatus,
  getPostureFacts,
  getRuns,
  streamCodeTurn,
  uploadAttachment,
} from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

const IMAGE = { id: "a1", name: "screen.png", kind: "image", chars: 0, note: "" };
const DOC = { id: "d1", name: "spec.pdf", kind: "document", chars: 4200, note: "" };

/**
 * Attaching is the one way content enters a conversation without having been typed, and the tray is
 * where the app has to be honest about what it actually made of the file. A document that yielded
 * nothing is not the same as one nobody opened; an upload that failed must not sit there looking
 * like it worked.
 */
describe("Code — attaching a file", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    vi.mocked(streamCodeTurn).mockImplementation(scriptTurn());
  });

  async function attach(result: typeof IMAGE) {
    const user = userEvent.setup();
    vi.mocked(uploadAttachment).mockResolvedValue(result);
    renderWithProviders(<Code />);
    const input = screen.getByLabelText("Attach") as HTMLInputElement;
    await user.upload(input, new File(["x"], result.name));
    return user;
  }

  it("sends the attachment's id with the turn, never its bytes", async () => {
    // An id rather than inline data: base64 in the body is re-sent on every retry and lands in
    // whatever logs the request lands in.
    const user = await attach(IMAGE);
    await screen.findByText("screen.png");

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "what is this?{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].attachments).toEqual(["a1"]);
  });

  it("says how much text a document actually yielded", async () => {
    await attach(DOC);

    await screen.findByText(/4200 chars/);
  });

  it("marks an attachment the server could not read, instead of showing it as fine", async () => {
    await attach({ ...DOC, chars: 0, note: "could not read: encrypted" });

    const chip = await screen.findByTitle("could not read: encrypted");
    expect(chip).toBeInTheDocument();
  });

  it("lets an attachment be taken back off before sending", async () => {
    const user = await attach(IMAGE);
    await user.click(await screen.findByRole("button", { name: /Remove screen.png/ }));

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "never mind{Enter}");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0].attachments).toEqual([]);
  });

  it("clears the tray after sending, so the next question does not carry it", async () => {
    // The failure this prevents is quiet and expensive: an unrelated follow-up silently re-sending
    // someone's PDF, paid for again, and answered as if it were still the subject.
    const user = await attach(IMAGE);
    await screen.findByText("screen.png");

    await user.type(screen.getByPlaceholderText(/^Ask about this code/), "what is this?{Enter}");
    await waitFor(() => expect(screen.queryByText("screen.png")).not.toBeInTheDocument());
  });
});
