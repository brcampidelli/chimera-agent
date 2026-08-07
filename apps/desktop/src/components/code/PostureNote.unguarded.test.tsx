import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PostureNote } from "@/components/code/PostureNote";
import { getPostureFacts } from "@/lib/api";
import { postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The chat is assembled without the taint ledger unless the user arms it, which means nothing marks
 * the conversation after it reads untrusted content and the tools that would otherwise start
 * refusing keep working. That default is deliberate — the registry is shared with the messaging
 * gateway, and arming it silently would take shell away from agents already running.
 *
 * A permissive default that says nothing is the one version of that decision which cannot be
 * defended. So this sentence is not decoration; it is the condition the choice was made under.
 */
describe("PostureNote — the guard that is not there", () => {
  beforeEach(() => vi.mocked(getPostureFacts).mockReset());

  it("says the conversation can still write after reading untrusted content", async () => {
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts({ unguarded: true }));
    renderWithProviders(<PostureNote workspace="/repo" reach="workspace" approval="suspicious" />);

    await screen.findByText(/can still write files/i);
  });

  it("stays quiet when the ledger is there, so the warning keeps meaning something", async () => {
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    renderWithProviders(<PostureNote workspace="/repo" reach="workspace" approval="suspicious" />);

    await screen.findByText(/Edits inside/i); // the ordinary sentence rendered
    expect(screen.queryByText(/can still write files/i)).not.toBeInTheDocument();
  });

  it("asks the server which surface it is, because the answer differs by surface", async () => {
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    renderWithProviders(
      <PostureNote workspace="/repo" reach="workspace" approval="suspicious" surface="chat" />,
    );

    await vi.waitFor(() =>
      expect(getPostureFacts).toHaveBeenCalledWith("workspace", "suspicious", "/repo", "chat"),
    );
  });
});
