import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Fusion } from "@/components/Fusion";
import { renderWithProviders } from "@/test/utils";

/**
 * This panel lives inside `Activity`, which is 288px wide.
 *
 * A slug like `openrouter/deepseek/deepseek-chat-v3.1` does not fit in that column. One row
 * truncated it with no way to read the rest; the other did not truncate at all and pushed the
 * badges beside it out of the row. And the stage badge rendered `stage.stage` — "panel" / "judge" /
 * "synth", the Literal the engine routes on — as three untranslated English words, on a screen that
 * has proper names for all three in ten languages.
 */
const LONG = "openrouter/deepseek/deepseek-chat-v3.1";

function report(over: Record<string, unknown>) {
  return {
    route_meta: {
      kind: "fusion",
      aggregation: "synth",
      early_stopped: false,
      diversity: null,
      panel: [],
      stages: [],
      ...over,
    },
  } as never;
}

describe("the fusion breakdown", () => {
  it("keeps a clipped slug readable", () => {
    renderWithProviders(
      <Fusion
        report={report({
          panel: [{ model: LONG, content: "an answer", prompt_tokens: 10, completion_tokens: 5 }],
          stages: [{ stage: "judge", model: LONG, prompt_tokens: 3, completion_tokens: 1 }],
        })}
      />,
    );

    // Both rows: the full value in the title, so truncation costs presentation and not information.
    expect(screen.getAllByTitle(LONG).length).toBe(2);
  });

  it("names the role instead of printing the engine's identifier", () => {
    renderWithProviders(
      <Fusion
        report={report({
          panel: [],
          stages: [{ stage: "synth", model: "m", prompt_tokens: 1, completion_tokens: 1 }],
        })}
      />,
    );

    // "synth" is a wire value. "Synthesizer" is what the app calls that role, in ten languages.
    expect(screen.getByText("Synthesizer")).toBeInTheDocument();
    expect(screen.queryByText("synth")).toBeNull();
  });

  it("translates the error badge instead of shipping one English word", () => {
    renderWithProviders(
      <Fusion
        report={report({
          panel: [{ model: "m", error: "rate limited", prompt_tokens: 0, completion_tokens: 0 }],
          stages: [],
        })}
      />,
    );

    // The key, not the literal: the assertion is that it goes THROUGH the dictionary.
    expect(screen.getByText("error")).toBeInTheDocument();
    expect(screen.getByText("rate limited")).toBeInTheDocument();
  });
});
