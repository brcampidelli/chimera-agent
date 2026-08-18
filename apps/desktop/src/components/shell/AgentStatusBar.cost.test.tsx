import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { AgentProvider, type AgentReport } from "@/lib/agent-context";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

/**
 * The number in the corner, and what it is a fraction OF.
 *
 * The bar has always shown what the last turn cost. It showed it against nothing — `~ $0.0123`,
 * which answers "how much" and never "how much of what I allowed". A ceiling that cannot be seen
 * being consumed is a ceiling nobody trusts enough to set.
 *
 * The third test is the one worth keeping honest. `Usage.tsx` prints "—" for a group where every
 * turn was unpriced rather than $0.0000, because an unknown cost rendered as a small number is a
 * lie that reads as good news. The bar inherits that: unknown stays unknown, with no denominator
 * beside it to size it against.
 */
function bar(report: AgentReport) {
  return renderWithProviders(
    <AgentProvider value={{ status: "done", report }}>
      <AgentStatusBar />
    </AgentProvider>,
  );
}

const SPENT: AgentReport = { prompt_tokens: 1200, completion_tokens: 300, usd: 0.0123 };

describe("AgentStatusBar — spend against a ceiling", () => {
  it("shows the ceiling the turn ran under beside what it spent", () => {
    bar({ ...SPENT, max_usd: 1 });

    expect(screen.getByRole("button", { name: "~ $0.0123 of $1.0000" })).toBeInTheDocument();
  });

  it("shows the cost alone when the turn ran uncapped", () => {
    // The default has to look exactly as it did. Every existing user runs without a ceiling, and a
    // denominator invented for them ("of $0.0000") would be a limit nobody set.
    bar(SPENT);

    expect(screen.getByRole("button", { name: "~ $0.0123" })).toBeInTheDocument();
  });

  it("says the cost is unavailable rather than sizing an unknown against a ceiling", () => {
    // A model with no list price reports no cost. Printing "$0.0000 of $1.0000" — or even
    // "unavailable of $1.0000" — invites reading the unknown as nearly nothing, which is the
    // direction that flatters the run and the direction the money actually goes.
    bar({ ...SPENT, usd: null, max_usd: 1 });

    expect(screen.getByRole("button", { name: "unavailable" })).toBeInTheDocument();
    expect(screen.queryByText(/\$1\.0000/)).not.toBeInTheDocument();
  });
});
