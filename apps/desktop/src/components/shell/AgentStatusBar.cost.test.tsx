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
  it("uses reported cache reads to show cache coverage", () => {
    bar({ ...SPENT, cache_read_tokens: 900, viewKey: "code" });

    expect(screen.getByText(/75% from cache|75% do cache/)).toBeInTheDocument();
  });

  it("compacts large token totals", () => {
    bar({ prompt_tokens: 1_500_000, completion_tokens: 51_517, usd: 0.04, viewKey: "code", cache_read_tokens: 1_470_000 });

    expect(screen.getByText(/1[.,]6 ?(M|mi) tokens \(98% (from|do) cache\)/)).toBeInTheDocument();
  });

  it("shows the ceiling the turn ran under beside what it spent", () => {
    bar({ ...SPENT, max_usd: 1, viewKey: "code" });

    expect(screen.getByRole("button", { name: "~ $0.0123 of $1.0000" })).toBeInTheDocument();
  });

  it("formats the cost to cents", () => {
    bar({ ...SPENT, viewKey: "code" });

    expect(screen.getByRole("button", { name: "~ $0.01" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /0\.0123/ })).not.toBeInTheDocument();
  });

  it("keeps four decimals under one cent so a cheap turn does not read as free", () => {
    bar({ ...SPENT, usd: 0.0031, viewKey: "code" });

    expect(screen.getByRole("button", { name: "~ $0.0031" })).toBeInTheDocument();
  });

  it("says the cost is unavailable rather than sizing an unknown against a ceiling", () => {
    // A model with no list price reports no cost. Printing "$0.0000 of $1.0000" — or even
    // "unavailable of $1.0000" — invites reading the unknown as nearly nothing, which is the
    // direction that flatters the run and the direction the money actually goes.
    bar({ ...SPENT, usd: null, max_usd: 1, viewKey: "code" });

    expect(screen.getByRole("button", { name: "unavailable" })).toBeInTheDocument();
    expect(screen.queryByText(/\$1\.0000/)).not.toBeInTheDocument();
  });
});
