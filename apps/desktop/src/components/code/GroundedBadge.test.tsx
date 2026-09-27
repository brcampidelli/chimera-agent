import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { TurnReceipt } from "@/components/code/Conversation";
import type { CodeTurnDone, GroundedCheck } from "@/lib/api";
import { DICTS } from "@/lib/i18n";
import { renderWithProviders } from "@/test/utils";

/**
 * The badge on an answer checked against its attached documents (study 26). The four outcomes a
 * person must be able to tell apart at a glance — verified, the sources don't cover it, a lexical
 * check only, unchecked — and the answer the verifier withheld, which stays readable.
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

const LOCAL = { backend: "local_logprob", model: "qwen3:4b", resolved_model: "qwen3:4b@Q4_K_M" };

function receipt(grounded: GroundedCheck | null | undefined) {
  renderWithProviders(<TurnReceipt done={{ ...BASE, grounded }} t={(k) => k} />);
}

describe("the grounded-answer badge", () => {
  it("says verified, with who verified it and how sure, on a supported answer", () => {
    receipt({
      outcome: "supported", verifier: LOCAL, label: "supported", p: 0.93,
      drafter_model: "openrouter/deepseek/deepseek-v4-flash-0731", drafter_measured: false,
    });

    const badge = screen.getByText("code.chat.grounded.verified");
    expect(badge).toHaveAttribute("title", expect.stringContaining("deepseek-v4-flash-0731 (unmeasured)"));
    expect(badge).toHaveAttribute("title", expect.stringContaining("qwen3:4b@Q4_K_M"));
    expect(badge).toHaveAttribute("title", expect.stringContaining("supported p=0.93"));
  });

  it("names the escalation, and says what it cost", () => {
    receipt({
      outcome: "escalated", verifier: LOCAL, escalated: true, escalated_model: "openrouter/openai/gpt-6-sol",
      usd_extra: 0.0123, withheld: ["the draft"],
    });

    const badge = screen.getByText("code.chat.grounded.escalated");
    expect(badge).toHaveAttribute("title", expect.stringContaining("→ openrouter/openai/gpt-6-sol"));
    expect(badge).toHaveAttribute("title", expect.stringContaining("+$0.0123"));
  });

  it("says the sources don't cover it, and keeps the withheld answer one click away", async () => {
    receipt({ outcome: "declined", decline_shipped: true, verifier: LOCAL, withheld: ["It uses 9 workers."] });

    expect(screen.getByText("code.chat.grounded.declined")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("code.chat.grounded.withheld"));
    expect(screen.getByText("It uses 9 workers.")).toBeVisible();
  });

  it("never draws an unchecked answer as verified", () => {
    receipt({ outcome: "unverified", verifier: LOCAL, halt: "ConnectError: refused" });

    expect(screen.queryByText("code.chat.grounded.verified")).not.toBeInTheDocument();
    expect(screen.getByText("code.chat.grounded.unavailable")).toHaveAttribute(
      "title",
      expect.stringContaining("ConnectError: refused"),
    );
  });

  it("says a lexical check is only lexical, and which verifier it fell back from", () => {
    receipt({
      outcome: "lexical",
      verifier: {
        backend: "lexical", model: "default_gate",
        fell_back_from: [{ backend: "local_logprob", model: "qwen3:4b", reason: "ollama_unreachable" }],
      },
    });

    expect(screen.queryByText("code.chat.grounded.verified")).not.toBeInTheDocument();
    expect(screen.getByText("code.chat.grounded.lexical")).toHaveAttribute(
      "title",
      expect.stringContaining("fallback from qwen3:4b (ollama_unreachable)"),
    );
  });

  it("says why a turn with documents was not checked", () => {
    receipt({ outcome: "not_applied", reason: "tool_calls" });

    expect(screen.getByText("code.chat.grounded.notApplied")).toHaveAttribute(
      "title",
      "code.chat.grounded.notApplied.toolCalls",
    );
  });

  it("says a task with documents was passed straight through, not checked", () => {
    receipt({ outcome: "not_applied", reason: "task" });

    expect(screen.queryByText("code.chat.grounded.verified")).not.toBeInTheDocument();
    expect(screen.getByText("code.chat.grounded.notApplied")).toHaveAttribute(
      "title",
      "code.chat.grounded.notApplied.task",
    );
  });

  it("draws nothing for a turn that attached nothing", () => {
    receipt(null);

    expect(screen.queryByText(/code\.chat\.grounded\./)).not.toBeInTheDocument();
  });

  it("has every badge string in every language", () => {
    const keys = Object.keys(DICTS.en).filter((k) => k.startsWith("code.chat.grounded."));
    expect(keys.length).toBe(11);
    for (const dict of Object.values(DICTS)) {
      for (const key of keys) expect((dict as Record<string, string>)[key]).toBeTruthy();
    }
  });
});
