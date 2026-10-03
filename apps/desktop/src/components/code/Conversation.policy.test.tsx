import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, TurnReceipt } from "@/components/code/Conversation";
import {
  getCodeSession,
  listShares,
  listWorks,
  streamCodeTurn,
  streamSessionLive,
  type CodeTurnDone,
  type CodeTurnHandlers,
  type CodeTurnInput,
  type SessionLiveFrame,
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

async function ask(text: string, opts: { fuse?: boolean; provider?: string } = {}) {
  renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
      posture={{ reach: "workspace" as never, approval: "ask" as never }}
      profile={"balanced" as never}
      model="openrouter/openai/gpt-4o"
      provider={opts.provider}
      onHandOff={() => {}}
      onBatch={() => {}}
      onEdited={() => {}}
      busyElsewhere={false}
      controls={null}
      onOpenFile={() => {}}
    />,
  );
  const user = userEvent.setup();
  if (opts.fuse) await user.click(await screen.findByRole("button", { name: /^fuse$/i }));
  await user.type(await screen.findByRole("textbox"), `${text}{Enter}`);
  return user;
}

/** Open the refusal's model list, pick the one other model the mock catalogue offers, and return
 *  what went out — the `calls`-th turn this screen sent (the refused one was the first). */
async function retryOnMid(user: ReturnType<typeof userEvent.setup>, calls = 2) {
  await user.click(await screen.findByRole("button", { name: /try with another model/i }));
  await user.click(await screen.findByText("Vendor: Mid"));
  await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(calls));
  return vi.mocked(streamCodeTurn).mock.calls[calls - 1]?.[0] as CodeTurnInput;
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

  it("redoes a fused turn on the one model picked, not on the panel that refused", async () => {
    // With Fusion on, the server hands the turn to the fusion engine, which ignores `model`: the
    // "retry on another model" went back to the same panel and judge, and the receipt named it.
    vi.mocked(streamCodeTurn).mockImplementation(failing(BLOCK));
    const user = await ask("escreve o conto", { fuse: true });
    expect((vi.mocked(streamCodeTurn).mock.calls[0]?.[0] as CodeTurnInput).fuse).toBe(true);

    const retry = await retryOnMid(user);

    expect(retry.fuse).toBe(false);
    expect(retry.fusion_panel).toBeUndefined();
    expect(retry.fusion_judge).toBeUndefined();
    expect(retry.fusion_synthesizer).toBeUndefined();
    expect(retry.model).toBe("openrouter/vendor/mid");
  });

  it("redoes the turn natively even when the composer has moved to an external agent", async () => {
    // An external agent picks its own model, so a retry sent with `provider` would not run on the
    // model the owner picked — while its receipt still said it had.
    vi.mocked(streamCodeTurn).mockImplementation(failing(BLOCK));
    const user = await ask("escreve o conto", { provider: "claude" });

    const retry = await retryOnMid(user);

    expect(retry.provider).toBeUndefined();
    expect(retry.model).toBe("openrouter/vendor/mid");
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

describe("a refusal on a turn this screen followed rather than sent", () => {
  // Coming back to a conversation mid-turn, the row is built from the `turn_started` frame, which
  // never carried the turn's files. A retry from there went out with `attachments: []`, silently:
  // the new model answered without the document, and the grounded check had nothing to check.
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset();
    vi.mocked(streamSessionLive).mockReset().mockResolvedValue(null);
    vi.mocked(listShares).mockReset().mockResolvedValue({ shares: [] });
    vi.mocked(listWorks).mockReset().mockResolvedValue({ works: [] });
    vi.mocked(getCodeSession).mockReset().mockResolvedValue({
      id: "s1",
      workspace: "/proj",
      exchanges: [],
      running_turn: {
        turn_id: "t1",
        session_id: "s1",
        workspace: "/proj",
        message: "lê o contrato",
        started_at: 1,
        live_since: 7,
        transcript_saved: false,
      },
    });
    localStorage.clear();
  });

  async function followRefused(attachmentCount: number) {
    let onFrame: ((f: SessionLiveFrame) => void) | null = null;
    vi.mocked(streamSessionLive).mockImplementation((_sid, _since, handler) => {
      onFrame = handler;
      return new Promise<string | null>(() => {});
    });
    renderWithProviders(
      <Conversation
        workspace="/proj"
        openFile={null}
        resumeSession="s1"
        posture={{ reach: "workspace" as never, approval: "ask" as never }}
        profile={"balanced" as never}
        onHandOff={() => {}}
        onBatch={() => {}}
        onEdited={() => {}}
        busyElsewhere={false}
        controls={null}
        onOpenFile={() => {}}
      />,
    );
    await waitFor(() => expect(onFrame).not.toBeNull());
    const say = (seq: number, event: string, payload: Record<string, unknown>) =>
      act(() => onFrame?.({ session_seq: seq, event, turn_id: "t1", author: "", payload }));
    await say(8, "turn_started", { message: "lê o contrato", attachment_count: attachmentCount });
    await say(9, "error", { message: "Blocked", reason: "content_policy", ...BLOCK });
    return screen.findByTestId("policy-blocked");
  }

  it("does not offer a retry that would go out without the turn's files, and says why", async () => {
    const card = await followRefused(1);

    expect(within(card).getByTestId("policy-files-missing")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /try with another model/i })).toBeNull();
  });

  it("offers it when the turn carried none, and sends none", async () => {
    await followRefused(0);
    const user = userEvent.setup();

    // The refused turn was sent by another screen: the retry is the first this one sends.
    const retry = await retryOnMid(user, 1);

    expect(retry.attachments).toEqual([]);
    expect(screen.queryByTestId("policy-files-missing")).toBeNull();
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
