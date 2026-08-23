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
// The SHARED mock, not a hand-written four-key one. This file listed exactly the helpers `Runs`
// called the day it was written, so the launcher gaining a model picker turned eleven tests about
// receipt evidence red over `getDoctor` — a function none of them care about. Same shape as the
// TooltipProvider breakage: a component added to a shared screen should not be a per-file edit.
vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());
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

/**
 * What a receipt is allowed to claim.
 *
 * These four moved here from a run panel folded under the Code screen — a second implementation of
 * this screen's launcher, with fewer features, now deleted. None of them was ever about that panel:
 * each one names something the interface must not say. They are asserted against the surface that
 * still says it.
 *
 * Two of the original seven did not survive the move, and it is worth writing down which. The panel
 * offered Accept and Discard on a finished run — a git revert scoped to the run's own changed paths
 * — with an honest disabled state outside a repo. This screen has no such control, so those two
 * assertions describe a capability that was removed rather than relocated. The Work screen's git tab
 * can still discard changes, just not scoped to one run.
 */
describe("what a receipt is allowed to claim", () => {
  it("renders the verifier's real captured output when the attempt produced some", async () => {
    await show(attempt({ verify_output: "1 passed in 0.42s" }));

    expect(await screen.findByText(/1 passed in 0.42s/)).toBeInTheDocument();
  });

  it("fabricates no verify panel when the verifier produced none", async () => {
    // An empty box where output goes reads as "the verifier said nothing", which is a different
    // claim from "we never captured any".
    await show(attempt({ verify_output: "", diff_summary: "1 file changed" }));

    expect(await screen.findByText(/1 file changed/)).toBeInTheDocument();
    expect(screen.queryByText(/passed in/)).not.toBeInTheDocument();
  });

  it("labels a reverted attempt as attempted-and-undone, never as applied", async () => {
    await show(attempt({ success: false, verified: false, reverted: true, evidence: "none" }));

    expect(await screen.findByText(/reverted/)).toBeInTheDocument();
  });

  it("does not label a successful attempt as reverted", async () => {
    await show(attempt({ reverted: false }));

    await waitFor(() => expect(mockGetRuns).toHaveBeenCalled());
    expect(screen.queryByText(/↩/)).not.toBeInTheDocument();
  });
});
