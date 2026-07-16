import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Code } from "@/components/Code";
import { captureScreenshot, getFsTree, getGitStatus, getRuns, streamRun } from "@/lib/api";
import { emptyTree, gitStatus } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** Type a URL into the browser-verify panel and click Capture. */
async function capture(url: string) {
  const user = userEvent.setup();
  renderWithProviders(<Code />);

  await user.type(screen.getByPlaceholderText("http://localhost:5173"), url);
  await user.click(screen.getByRole("button", { name: "Capture" }));
  return user;
}

describe("Code — the browser screenshot panel", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(streamRun).mockImplementation(async () => {});
  });

  it("captures the URL the user gave", async () => {
    vi.mocked(captureScreenshot).mockResolvedValue({ ok: true, id: "shot_1", error: null });
    await capture("http://localhost:5173");

    expect(captureScreenshot).toHaveBeenCalledWith("http://localhost:5173", null);
  });

  it("renders the stored artifact once the capture succeeded", async () => {
    vi.mocked(captureScreenshot).mockResolvedValue({ ok: true, id: "shot_1", error: null });
    await capture("http://localhost:5173");

    const img = await screen.findByRole("img", { name: "Screenshot of the URL you gave" });
    expect(img).toHaveAttribute("src", "/api/artifacts/shot_1");
  });

  it("captions the shot as of the URL you gave, not as an agent verification", async () => {
    vi.mocked(captureScreenshot).mockResolvedValue({ ok: true, id: "shot_1", error: null });
    await capture("http://localhost:5173");

    await screen.findByRole("img", { name: "Screenshot of the URL you gave" });
    expect(screen.getByText(/Screenshot of/)).toBeInTheDocument();
    expect(screen.getByText("http://localhost:5173")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Capture a full-page screenshot of a URL — an honest snapshot of whatever it renders (not a claim the agent verified anything).",
      ),
    ).toBeInTheDocument();
  });

  it("shows the backend's real error and no image when the capture failed", async () => {
    vi.mocked(captureScreenshot).mockResolvedValue({
      ok: false,
      id: null,
      error: "playwright install chromium",
    });
    await capture("http://localhost:5173");

    expect(await screen.findByText("playwright install chromium")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Screenshot of the URL you gave" })).not.toBeInTheDocument();
  });

  it("falls back to an honest failure message when the backend gives no reason", async () => {
    vi.mocked(captureScreenshot).mockResolvedValue({ ok: false, id: null, error: null });
    await capture("http://localhost:5173");

    expect(await screen.findByText("Couldn't capture a screenshot.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Screenshot of the URL you gave" })).not.toBeInTheDocument();
  });

  it("reports a thrown capture as a failure rather than a broken image", async () => {
    vi.mocked(captureScreenshot).mockRejectedValue(new Error("network error"));
    await capture("http://localhost:5173");

    expect(await screen.findByText("Couldn't capture a screenshot.")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Screenshot of the URL you gave" })).not.toBeInTheDocument();
  });

  it("refuses to capture without a URL", () => {
    renderWithProviders(<Code />);

    expect(screen.getByRole("button", { name: "Capture" })).toBeDisabled();
    expect(captureScreenshot).not.toHaveBeenCalled();
  });

  it("shows no image before anything has been captured", () => {
    renderWithProviders(<Code />);

    expect(screen.queryByRole("img", { name: "Screenshot of the URL you gave" })).not.toBeInTheDocument();
  });
});
