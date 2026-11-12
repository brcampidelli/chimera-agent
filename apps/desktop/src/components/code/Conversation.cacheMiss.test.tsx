import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TurnReceipt } from "@/components/code/Conversation";
import { shownMissCause } from "@/lib/cache-miss";
import type { CodeTurnDone } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

/** The turn receipt names why the cache did not answer, and says when a pause shortened the history.
 *
 *  The text is asserted through the key the receipt asks for, so a rename of a cause shows here. */

const DONE: CodeTurnDone = {
  answer: "ok",
  steps: 1,
  stopped_reason: "final",
  tool_names: [],
  model: "anthropic/claude-haiku-5.5",
  prompt_tokens: 1,
  completion_tokens: 1,
  usd: 0.001,
  route_meta: null,
  context_peak_tokens: 0,
};

const t = (key: string, params?: Record<string, string | number>) =>
  params ? `${key}:${JSON.stringify(params)}` : key;

describe("shownMissCause", () => {
  it("draws a cause only for a turn that read nothing from the cache", () => {
    expect(shownMissCause("ttl_expired", 0)).toBe("ttl_expired");
    expect(shownMissCause("ttl_expired", 4096)).toBeNull();
    expect(shownMissCause("ttl_expired", null)).toBeNull();
  });

  it("never draws `none`, an absent cause, or a missing count", () => {
    expect(shownMissCause("none", 0)).toBeNull();
    expect(shownMissCause(null, 0)).toBeNull();
    expect(shownMissCause(undefined, 0)).toBeNull();
  });
});

describe("TurnReceipt cache miss", () => {
  it("says in a few words why the cache missed on a turn that read nothing", () => {
    renderWithProviders(
      <TurnReceipt done={{ ...DONE, cache_read_tokens: 0, cache_miss_cause: "ttl_expired" }} t={t} />,
    );
    expect(screen.getByText("code.chat.miss.ttl_expired")).toBeInTheDocument();
  });

  it("says nothing about a miss on a turn that read from the cache", () => {
    renderWithProviders(
      <TurnReceipt done={{ ...DONE, cache_read_tokens: 2048, cache_miss_cause: "none" }} t={t} />,
    );
    expect(screen.queryByText(/code\.chat\.miss\./)).not.toBeInTheDocument();
  });

  it("names no cause when the provider was silent", () => {
    renderWithProviders(
      <TurnReceipt done={{ ...DONE, cache_read_tokens: null, cache_miss_cause: null }} t={t} />,
    );
    expect(screen.queryByText(/code\.chat\.miss\./)).not.toBeInTheDocument();
  });

  it("shows the shortened history with its estimated sizes when a pause shortened it", () => {
    renderWithProviders(
      <TurnReceipt
        done={{
          ...DONE,
          idle_compaction: {
            gap_seconds: 900,
            ttl_seconds: 300,
            tokens_before: 120000,
            tokens_after: 9000,
            saved_tokens: 111000,
            estimated: true,
          },
        }}
        t={t}
      />,
    );
    expect(
      // The sizes arrive already formatted by the locale (`num`), so they are matched as text, not digits.
      screen.getByText(/code\.chat\.idleCompacted:\{"before":"120[^"]*","after":"9[^"]*"\}/),
    ).toBeInTheDocument();
  });

  it("says nothing of a shortening that did not happen", () => {
    renderWithProviders(<TurnReceipt done={{ ...DONE, idle_compaction: null }} t={t} />);
    expect(screen.queryByText(/code\.chat\.idleCompacted/)).not.toBeInTheDocument();
  });
});
