import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns, streamCodeTurn } from "@/lib/api";
import type { CodeBrowserFrame } from "@/lib/api";
import { emptyTree, gitStatus, postureFacts, scriptTurn } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The agent's browser, drawn under the turn that drove it.
 *
 * One JPEG per browser action arrives on the turn's stream; the panel shows the LAST one — where the
 * browser is, not where it was — with the page's address as text beside it, because a picture of a
 * page is a claim about where the browser is and the URL is the part a person can check. A turn
 * with no browser action draws nothing: an empty frame would be a claim about a page nobody opened.
 */
const JPEG = "/9j/4AAQSkZJRgABAQAAAQABAAD/stub";

function frame(n: number, url: string, action = "navigate"): CodeBrowserFrame {
  return { action, url, title: `Page ${n}`, width: 1280, height: 720, jpeg: JPEG, n };
}

async function turnWithFrames(frames: CodeBrowserFrame[]) {
  const user = userEvent.setup();
  vi.mocked(streamCodeTurn).mockImplementation(scriptTurn({ browser: frames }));
  renderWithProviders(<Code />);
  await user.type(screen.getByPlaceholderText(/^Ask about this code/), "open the docs{Enter}");
  return user;
}

describe("Code — the agent's browser on screen", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
  });

  it("draws the picture with the page's address as text beside it", async () => {
    await turnWithFrames([frame(1, "https://example.com/docs")]);

    const view = await screen.findByTestId("browser-view");
    const img = within(view).getByRole("img");
    expect(img.getAttribute("src")).toBe(`data:image/jpeg;base64,${JPEG}`);
    expect(img.getAttribute("alt")).toContain("Page 1");
    expect(within(view).getByText("https://example.com/docs")).toBeInTheDocument();
  });

  it("shows where the browser IS — the last frame, not the first", async () => {
    await turnWithFrames([
      frame(1, "https://example.com/"),
      frame(2, "https://example.com/docs", "click"),
      frame(3, "https://example.com/docs/install", "click"),
    ]);

    const view = await screen.findByTestId("browser-view");
    expect(within(view).getByText("https://example.com/docs/install")).toBeInTheDocument();
    expect(within(view).queryByText("https://example.com/")).toBeNull();
    expect(screen.getAllByTestId("browser-view")).toHaveLength(1);
    expect(within(view).getByText(/frame 3/)).toBeInTheDocument();
  });

  it("draws nothing for a turn that never opened the browser", async () => {
    await turnWithFrames([]);

    expect(await screen.findByText(/open the docs/)).toBeInTheDocument();
    expect(screen.queryByTestId("browser-view")).toBeNull();
  });
});
