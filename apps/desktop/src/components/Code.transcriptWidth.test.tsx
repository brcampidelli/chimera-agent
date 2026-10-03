import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Code } from "@/components/Code";
import { getFsTree, getGitStatus, getPostureFacts, getRuns } from "@/lib/api";
import { defaultLayout } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";
import { emptyTree, gitStatus, postureFacts } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/** The conversation's width comes from the layout (study 29, P1.2); medium is the width it always had. */
describe("Code — the conversation's width", () => {
  beforeEach(() => {
    vi.mocked(getFsTree).mockResolvedValue(emptyTree());
    vi.mocked(getGitStatus).mockResolvedValue(gitStatus());
    vi.mocked(getRuns).mockResolvedValue([]);
    vi.mocked(getPostureFacts).mockResolvedValue(postureFacts());
    localStorage.clear();
  });

  it("runs at the width it always had when nothing was chosen", async () => {
    renderWithProviders(<Code />);
    const log = await screen.findByRole("log");
    expect(log).toHaveClass("max-w-3xl");
  });

  it("runs at the width the layout keeps", async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ ...defaultLayout(), transcriptWidth: "wide" }));
    renderWithProviders(<Code />);
    const log = await screen.findByRole("log");
    expect(log).toHaveClass("max-w-5xl");
    expect(log).not.toHaveClass("max-w-3xl");
  });
});
