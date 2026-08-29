import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Governance } from "@/components/Governance";
import { getGovernanceAudit, getGovernanceInjection } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getGovernanceAudit: vi.fn(),
  getGovernanceInjection: vi.fn(),
  // The screen gained an execution-boundary panel; a mock missing it makes the whole screen throw,
  // which is this file's tests failing about something they are not testing.
  getSandboxState: vi.fn(),
}));

/**
 * A security scoreboard is read as a verdict on everything the reader has heard of, not on the one
 * layer it actually exercises. Two ways that goes wrong here, and both are silent.
 *
 * The measured layer — the taint ledger's adaptive narrowing — is switchable. With
 * CHIMERA_TAINT_NARROW=0 the defended column keeps reporting the same number while describing a
 * configuration nobody on the machine is running. And the trust kernel's BLOCK/REVIEW rules exist in
 * the codebase but are wired into the guarded CLI runs only, so nothing on this screen measures
 * them; a good score is exactly what makes someone assume otherwise.
 */
const REPORT = {
  total_attacks: 6,
  defended_asr: 0.17,
  undefended_asr: 1,
  defended_block_rate: 0.83,
  undefended_block_rate: 0,
  by_category: [{ category: "exfil", defended_asr: 0.5, undefended_asr: 1, count: 2 }],
  attacks: [
    {
      id: "http_exfil",
      category: "exfil",
      harmful_tool: "http_get",
      blocked_defended: false,
      blocked_undefended: false,
    },
  ],
  leaks_defended: ["http_exfil"],
  defense: "taint_narrowing",
  armed: true,
  trust_kernel: false,
};

describe("Governance — what it is measuring, and what it is not", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getGovernanceAudit).mockResolvedValue({
      events: [],
      count: 0,
      populated: false,
    } as never);
    vi.mocked(getGovernanceInjection).mockResolvedValue(REPORT as never);
  });

  it("names the layer this score does not cover, even when the score is good", async () => {
    renderWithProviders(<Governance />);
    // "nothing here measures", not "not on this path": the rules DO run on the run and turn
    // endpoints once CHIMERA_GOVERNANCE is set, and the old sentence went stale in ten languages
    // at once. What stays true in every configuration is that this suite does not exercise them.
    await screen.findByText(/Nothing here measures the BLOCK\/REVIEW policy rules/i);
  });

  it("says the measured defence is switched off, before showing the numbers it produced", async () => {
    vi.mocked(getGovernanceInjection).mockResolvedValue({ ...REPORT, armed: false } as never);
    renderWithProviders(<Governance />);
    const warning = await screen.findByText(/Switched OFF in this install/i);

    // Order matters more than presence. A caveat placed under a defended score arrives after the
    // reader has already formed the belief it exists to prevent.
    const score = screen.getByText("17%");
    expect(warning.compareDocumentPosition(score) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("stays quiet about the defence when it is actually armed", async () => {
    renderWithProviders(<Governance />);
    await screen.findByText("17%");
    expect(screen.queryByText(/Switched OFF in this install/i)).not.toBeInTheDocument();
  });

  it("reads an empty trail as nothing having happened, not as nothing watching", async () => {
    // The app writes an entry whenever a defence fires now. Before that it wrote none at all, and
    // this panel told people the desktop chat "isn't governed by default" — which is the reading
    // that makes an empty log worthless either way.
    renderWithProviders(<Governance />);
    await screen.findByText(/nothing has been narrowed, escalated or suppressed/i);
    expect(screen.queryByText(/isn't governed by default/i)).not.toBeInTheDocument();
  });
});
