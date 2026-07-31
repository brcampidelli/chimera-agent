import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Runs } from "@/components/Runs";
import { getPausedRuns, getRuns } from "@/lib/api";
import { attempt, receipt } from "@/test/code-api-mock";
import { renderWithProviders } from "@/test/utils";

/**
 * The attempt row used to render a bare ✓/✗ driven by `verified` alone.
 *
 * That collapsed a three-way verdict into two on the one surface a human actually looks at: an
 * attempt approved by a real workspace diff plus a manager review rendered identically to one where
 * an LLM approved prose and nothing else was checked — both ✗ — and "we could not measure whether
 * anything changed" had no representation at all, so it read as "nothing changed".
 *
 * These tests exist so that collapse has to be a deliberate, failing edit rather than a default.
 */

// Runs embeds RunLauncher, which asks for parked runs on mount — stubbed to empty so these tests
// exercise the receipt rows and nothing else.
vi.mock("@/lib/api", () => ({
  getRuns: vi.fn(),
  getPausedRuns: vi.fn().mockResolvedValue([]),
  startRun: vi.fn(),
  cancelRun: vi.fn(),
}));
const mockGetRuns = vi.mocked(getRuns);

beforeEach(() => vi.clearAllMocks());

async function show(...attempts: ReturnType<typeof attempt>[]) {
  mockGetRuns.mockResolvedValue([receipt({ attempts })]);
  vi.mocked(getPausedRuns).mockResolvedValue([]);
  renderWithProviders(<Runs />);
  await waitFor(() => expect(mockGetRuns).toHaveBeenCalled());
}

describe("an attempt row names who approved it", () => {
  it("distinguishes executable evidence from an LLM approving prose", async () => {
    await show(
      attempt({ index: 1, evidence: "verifier", verified: true }),
      attempt({ index: 2, evidence: "manager", verified: false }),
    );
    expect(await screen.findByText("verified by tests")).toBeInTheDocument();
    expect(screen.getByText("review only")).toBeInTheDocument();
  });

  it("does not report a diff-approved attempt as unverified", async () => {
    // verified=false + success=true is exactly the row that read as a plain ✗ before. The label is
    // the only thing that names the authority, so it has to be present and specific.
    await show(attempt({ evidence: "diff+manager", verified: false, success: true }));
    expect(await screen.findByText("changed files + review")).toBeInTheDocument();
    expect(screen.queryByText("review only")).not.toBeInTheDocument();
  });
});

describe("the third state stays visible", () => {
  it("shows an unmeasurable change as unknown, not as an empty diff", async () => {
    await show(attempt({ diff_productive: null, diff_summary: "" }));
    expect(await screen.findByText("change not measured")).toBeInTheDocument();
    expect(screen.queryByText("no file changed")).not.toBeInTheDocument();
  });

  it("shows a measured-empty diff as its own, different thing", async () => {
    await show(attempt({ diff_productive: false, diff_summary: "" }));
    expect(await screen.findByText("no file changed")).toBeInTheDocument();
    expect(screen.queryByText("change not measured")).not.toBeInTheDocument();
  });

  it("says nothing about the diff when the workspace measurably changed", async () => {
    await show(attempt({ diff_productive: true }));
    await waitFor(() => expect(screen.queryByText("no file changed")).not.toBeInTheDocument());
    expect(screen.queryByText("change not measured")).not.toBeInTheDocument();
  });
});

describe("out-of-checkout effects", () => {
  it("names the side effects an attempt performed, so an empty diff is not read as 'nothing happened'", async () => {
    await show(attempt({ diff_productive: false, side_effects: ["send_email", "http_post"] }));
    expect(await screen.findByText(/send_email, http_post/)).toBeInTheDocument();
  });

  it("stays quiet for a run that only touched files", async () => {
    await show(attempt({ side_effects: [] }));
    await waitFor(() => expect(screen.queryByText(/side effects/i)).not.toBeInTheDocument());
  });
});
