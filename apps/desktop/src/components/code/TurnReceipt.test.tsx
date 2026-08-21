import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TurnReceipt } from "@/components/code/Conversation";
import type { CodeTurnDone } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

/**
 * What a finished turn says it did — and, more to the point, what it must not say.
 *
 * Two Settings toggles used to fire nothing at all from this app: an explicit "remember that…" went
 * to a route no screen calls, and "Tidy memory" was read only inside the CLI's own REPL loop. Now
 * both happen on the coding turn, which means a conversation can silently change what the agent
 * knows in every FUTURE conversation. A change with that reach gets a badge.
 */
const BASE: CodeTurnDone = {
  answer: "ok",
  steps: 1,
  stopped_reason: "final",
  tool_names: [],
  model: "m",
  prompt_tokens: 10,
  completion_tokens: 5,
  usd: 0.001,
  route_meta: null,
  context_peak_tokens: 0,
};

function receipt(extra: Partial<CodeTurnDone>) {
  renderWithProviders(<TurnReceipt done={{ ...BASE, ...extra }} t={(k) => k} />);
}

describe("the turn receipt", () => {
  it("says a durable fact was saved", () => {
    receipt({ memory_saved: "meu voo é dia 12" });

    expect(screen.getByText("code.chat.remembered")).toBeInTheDocument();
  });

  it("carries the fact itself, because memory can hold a sentence a chip cannot", () => {
    receipt({ memory_saved: "meu voo é dia 12" });

    expect(screen.getByTitle("meu voo é dia 12")).toBeInTheDocument();
  });

  it("says how many were merged when tidying ran", () => {
    receipt({ memory_saved: "x", memory_consolidated: 3 });

    expect(screen.getByText("code.chat.rememberedTidied")).toBeInTheDocument();
    expect(screen.queryByText("code.chat.remembered")).not.toBeInTheDocument();
  });

  it("stays quiet on a turn that saved nothing", () => {
    // The failure worth guarding: a badge that always renders would tell a user their words are
    // being written down on every turn, which is the one thing this narrow write is not doing.
    receipt({ memory_consolidated: 0 });

    expect(screen.queryByText("code.chat.remembered")).not.toBeInTheDocument();
    expect(screen.queryByText("code.chat.rememberedTidied")).not.toBeInTheDocument();
  });

  it("does not report a count as a save", () => {
    receipt({ memory_saved: null, memory_consolidated: 4 });

    expect(screen.queryByText("code.chat.rememberedTidied")).not.toBeInTheDocument();
  });
});
