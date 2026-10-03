import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, TurnReceipt } from "@/components/code/Conversation";
import {
  streamCodeTurn,
  type CodeTurnDone,
  type CodeTurnHandlers,
  type CodeTurnInput,
} from "@/lib/api";
import { policyBlockOf, type PolicyBlockInfo } from "@/lib/policy-block";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * A turn the provider refused on content policy (study 29 P5.7).
 *
 * The error card said "That turn failed." for a refusal exactly as for a crash, named no model, and
 * offered "Try again" — on the model that had just refused. What is pinned: a refusal is said as one
 * with the model, the route and the provider's request id; its way forward is "Try with another
 * model", which does NOTHING until the owner picks one; the pick goes out once, on the retry only,
 * with the same message and the refusal it answers; any other failure keeps its own card; and the
 * retry's receipt says it was redone, on what, by the owner's choice.
 */

const BLOCK: PolicyBlockInfo = {
  model: "openrouter/openai/gpt-4o",
  provider: "OpenAI",
  request_id: "req_9f2c41",
};

function failing(block?: PolicyBlockInfo) {
  return (_req: CodeTurnInput, h: CodeTurnHandlers) => {
    h.onSession?.("s1", "t1");
    h.onError?.(block ? "Blocked by the provider's content policy" : "upstream said no", block);
    return Promise.resolve();
  };
}

async function ask(text: string) {
  renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      model="openrouter/openai/gpt-4o"
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={false}
      controls={null}
      onOpenFile={() => {}}
    />,
  );
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox"), `${text}{Enter}`);
  return user;
}

describe("a turn the provider refused on content policy", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset();
    localStorage.clear();
  });

  it("says it was refused, by what, with the provider's request id", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(failing(BLOCK));
    await ask("escreve o conto");

    const card = await screen.findByTestId("policy-blocked");
    expect(within(card).getByText(/content policy on openrouter\/openai\/gpt-4o/i)).toBeInTheDocument();
    expect(within(card).getByText(/Served by OpenAI/)).toBeInTheDocument();
    expect(within(card).getByText("req_9f2c41")).toBeInTheDocument();
    expect(within(card).getByText(/Nothing was retried/)).toBeInTheDocument();
    // Not "Try again" on the model that just refused: the way forward is a choice of model.
    expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  });

  it("does nothing until the owner picks a model, then redoes the same turn on it once", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(failing(BLOCK));
    const user = await ask("escreve o conto");

    await user.click(await screen.findByRole("button", { name: /try with another model/i }));
    // The list is open; nothing has been sent yet. Opening it is not consent to anything.
    expect(streamCodeTurn).toHaveBeenCalledTimes(1);

    await user.click(await screen.findByText("Vendor: Mid"));

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    const retry = vi.mocked(streamCodeTurn).mock.calls[1]?.[0] as CodeTurnInput;
    expect(retry.message).toBe("escreve o conto");
    expect(retry.model).toBe("openrouter/vendor/mid");
    expect(retry.retry_of).toEqual({
      blocked_model: "openrouter/openai/gpt-4o",
      request_id: "req_9f2c41",
    });
  });

  it("keeps the ordinary card and its Try again for any other failure", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(failing());
    const user = await ask("escreve o conto");

    expect(await screen.findByText("That turn failed.")).toBeInTheDocument();
    expect(screen.queryByTestId("policy-blocked")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /try again/i }));
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    // An ordinary retry is not a retry of a refusal, and its receipt must not say it was.
    expect((vi.mocked(streamCodeTurn).mock.calls[1]?.[0] as CodeTurnInput).retry_of).toBeUndefined();
  });
});

describe("the error frame", () => {
  it("is a refusal only when it says content_policy", () => {
    expect(policyBlockOf({ message: "x" })).toBeUndefined();
    expect(policyBlockOf({ message: "x", reason: "rate_limit", model: "m" })).toBeUndefined();
    expect(
      policyBlockOf({ reason: "content_policy", model: "m", provider: null, request_id: "r" }),
    ).toEqual({ model: "m", provider: null, request_id: "r" });
  });
});

describe("the receipt of a retried refusal", () => {
  const DONE: CodeTurnDone = {
    answer: "ok",
    steps: 1,
    stopped_reason: "final",
    tool_names: [],
    model: "openrouter/vendor/mid",
    prompt_tokens: 1,
    completion_tokens: 1,
    usd: 0.001,
    route_meta: null,
    context_peak_tokens: 0,
  };

  it("says the turn was redone on another model by the owner's choice", () => {
    renderWithProviders(
      <TurnReceipt
        done={{
          ...DONE,
          policy_retry: { blocked_model: "openrouter/openai/gpt-4o", request_id: "req_9f2c41" },
        }}
        t={(k, p) => `${k}:${JSON.stringify(p ?? {})}`}
      />,
    );

    expect(
      screen.getByText(/code\.chat\.policy\.redone.*openrouter\/openai\/gpt-4o.*openrouter\/vendor\/mid/),
    ).toBeInTheDocument();
  });

  it("says nothing of the kind on any other turn", () => {
    renderWithProviders(<TurnReceipt done={DONE} t={(k) => k} />);

    expect(screen.queryByText(/code\.chat\.policy\.redone/)).not.toBeInTheDocument();
  });
});
