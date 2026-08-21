import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Governance } from "@/components/Governance";
import { getGovernanceAudit, getGovernanceInjection } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getGovernanceAudit: vi.fn(),
  getGovernanceInjection: vi.fn(),
}));

/**
 * Every audit entry carries the digest of the one before it. That cost is paid on every write for
 * one property — this log has not been edited — and nothing in the codebase ever walked the chain
 * to find out. An unverified chain is bookkeeping, not evidence: it detects tampering the way an
 * unread smoke alarm detects fire.
 *
 * So the result is now on the screen, and it is on the screen in BOTH directions. A check whose
 * outcome is shown only when it fails cannot be told apart, by the reader, from one that never ran.
 */
const REPORT = {
  total_attacks: 6,
  defended_asr: 0.17,
  undefended_asr: 1,
  defended_block_rate: 0.83,
  undefended_block_rate: 0,
  by_category: [],
  attacks: [],
  leaks_defended: [],
  defense: "taint_narrowing",
  armed: true,
  trust_kernel: false,
};

function audit(chain: Record<string, unknown>) {
  vi.mocked(getGovernanceAudit).mockResolvedValue({
    events: [{ seq: 1, type: "taint_narrowed", summary: "tool=write_file" }],
    count: 1,
    populated: true,
    chain,
  } as never);
}

describe("the audit log's own tamper-evidence", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getGovernanceInjection).mockResolvedValue(REPORT as never);
  });

  it("says the chain holds, and how much of it was actually checked", async () => {
    audit({ ok: true, checked: 12, unchained: 0, broken_at: null, reason: "ok" });
    renderWithProviders(<Governance />);

    await screen.findByText(/chain intact/i);
    await screen.findByText(/12 entries verified/i);
  });

  it("names the entry where the chain breaks, not just that it broke", async () => {
    audit({
      ok: false,
      checked: 3,
      unchained: 0,
      broken_at: 3,
      reason: "entry content does not match its digest",
    });
    renderWithProviders(<Governance />);

    await screen.findByText(/chain broken/i);
    // broken_at is a zero-based index; a reader counting rows starts at one.
    await screen.findByText(/Entry 4 does not match/i);
  });

  it("does not fold what it could not check into what it could", async () => {
    // Entries older than the chain cannot be verified either way. "We could not check these" is not
    // "these are fine", and a pass that quietly swallows them says the second thing.
    audit({ ok: true, checked: 5, unchained: 2, broken_at: null, reason: "ok, 2 unchained" });
    renderWithProviders(<Governance />);

    await screen.findByText(/2 older entries predate the chain/i);
  });

  it("says nothing about a chain on a log with no entries", async () => {
    // A fresh install has an empty log. Announcing "chain intact" over nothing would be a
    // reassurance about a thing that does not exist yet.
    vi.mocked(getGovernanceAudit).mockResolvedValue({
      events: [],
      count: 0,
      populated: false,
      chain: { ok: true, checked: 0, unchained: 0, broken_at: null, reason: "ok" },
    } as never);
    renderWithProviders(<Governance />);

    await screen.findByText(/No audit events/i);
    expect(screen.queryByText(/chain intact/i)).not.toBeInTheDocument();
  });
});
