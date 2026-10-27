import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Decisions } from "@/components/Decisions";
import { getDecisions } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", () => ({
  getDecisions: vi.fn(),
  labelDecision: vi.fn(async () => ({ ok: true })),
}));

const BENCH = "bench/jev_decisions/RESULTS.md";

/**
 * The declared point's bench path has no space to break at, so it ran past the card's right edge
 * and out of the column. It may now break anywhere, and the line keeps the card's width.
 */
describe("a decision point's bench path stays inside its card", () => {
  beforeEach(() => {
    vi.mocked(getDecisions).mockResolvedValue({
      review_at: 0.5,
      allow_below: 0.3,
      specs: [{
        name: "governance.danger", escalation: "review", mode: "enforce", bench: BENCH,
        threshold: null, surfaces: [], description: "Is this shell action dangerous?",
      }],
      groups: [],
      recent: [],
      alerts: [],
    } as never);
  });

  it("lets the bench path break, so a long unbroken word cannot push past the card", async () => {
    renderWithProviders(<Decisions />);

    const bench = await screen.findByText(BENCH);
    expect(bench).toHaveClass("break-all", "min-w-0");
  });
});
