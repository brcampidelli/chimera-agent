import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentStatusBar } from "@/components/shell/AgentStatusBar";
import { AgentProvider, type AgentReport } from "@/lib/agent-context";
import { cacheWarningText } from "@/lib/cache-miss";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/components/VersionBadge", () => ({ VersionBadge: () => null }));

/** The warning is about the conversation's rate, and it is drawn on the turn that crossed under the
 *  threshold, with the cause that cost the most tokens. A turn that carries none draws none. */
function bar(report: AgentReport) {
  return renderWithProviders(
    <AgentProvider value={{ status: "done", report }}>
      <AgentStatusBar />
    </AgentProvider>,
  );
}

const TURN: AgentReport = {
  prompt_tokens: 1000,
  completion_tokens: 100,
  usd: 0.01,
  cache_read_tokens: 600,
  viewKey: "code",
};

describe("AgentStatusBar — cache warning", () => {
  it("names the rate, the threshold and the cause on the turn that crossed", () => {
    bar({ ...TURN, cache_warning: { rate: 0.6, threshold: 0.8, cause: "ttl_expired", turns: 4 } });

    const warning = screen.getByTestId("cache-warning");
    expect(warning).toHaveTextContent(/60%/);
    expect(warning).toHaveTextContent(/80%/);
    expect(warning).toHaveTextContent(/pause|pausa|expired|expir/i);
  });

  it("draws nothing on a turn that carries no warning", () => {
    bar(TURN);
    expect(screen.queryByTestId("cache-warning")).not.toBeInTheDocument();
    bar({ ...TURN, cache_warning: null });
    expect(screen.queryByTestId("cache-warning")).not.toBeInTheDocument();
  });
});

describe("cacheWarningText", () => {
  const t = (key: string, params?: Record<string, string | number>) =>
    params ? `${key}:${JSON.stringify(params)}` : key;

  it("is null without a warning", () => {
    expect(cacheWarningText(null, t, String)).toBeNull();
    expect(cacheWarningText(undefined, t, String)).toBeNull();
  });

  it("puts the cause through the same labels the receipt uses", () => {
    const text = cacheWarningText({ rate: 0.6, threshold: 0.8, cause: "tools_changed", turns: 3 }, t, (n) => `${n}%`);
    expect(text).toContain("code.chat.miss.tools_changed");
    expect(text).toContain('"rate":"60%"');
  });

  it("draws an unlisted cause as not identified", () => {
    const text = cacheWarningText({ rate: 0.5, threshold: 0.8, cause: "none", turns: 3 }, t, String);
    expect(text).toContain("code.chat.miss.unknown");
  });
});

describe("AgentStatusBar - cache warning from an older server", () => {
  it("draws nothing when the receipt has no cache_warning field at all", () => {
    bar({ ...TURN });
    expect(screen.queryByTestId("cache-warning")).not.toBeInTheDocument();
  });
});
