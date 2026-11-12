import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { listCodeEnvironments, streamCodeTurn, type CodeTurnDone, type CodeTurnHandlers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => ({
  ...(await import("@/test/code-api-mock")).makeCodeApiMock(),
  listCodeEnvironments: vi.fn(),
}));

/**
 * The environment rides on a conversation's FIRST turn and on no other: the server fixes it there and
 * refuses a change, so a later turn that repeated a stale pick would only ever be a way to be refused.
 */
const LOCAL = { id: "local", name: "Local", kind: "local", params: {}, builtin: true, available: true };
const UBUNTU = { id: "e1", name: "Ubuntu dev", kind: "wsl", params: {}, builtin: false, available: true };

function mount() {
  return renderWithProviders(
    <Conversation
      workspace="/proj"
      openFile={null}
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
}

async function send(text: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByRole("textbox"), text);
  await user.keyboard("{Enter}");
}

beforeEach(() => {
  vi.mocked(streamCodeTurn).mockReset();
  vi.mocked(listCodeEnvironments).mockReset().mockResolvedValue({
    environments: [LOCAL, UBUNTU],
    kinds: [{ kind: "wsl", available: true }],
    defaults: {},
    project_default: "",
  });
  vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
    h.onSession?.("s1", "turn-1");
    h.onDone?.({
      answer: "done", steps: 1, stopped_reason: "final", tool_names: [], model: "",
      prompt_tokens: 0, completion_tokens: 0, usd: null, tainted: false, memory_facts_used: 0,
      memory_layer: "", fused: false,
    } as unknown as CodeTurnDone);
  });
});

describe("the environment of a conversation", () => {
  it("is sent with the first turn when the owner picked one", async () => {
    mount();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Environment" }), "e1");
    await send("run the tests");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).toMatchObject({ environment: "e1" });
  });

  it("is left out when nothing was picked, so the server's default decides", async () => {
    mount();
    await send("hello");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    expect(vi.mocked(streamCodeTurn).mock.calls[0][0]).not.toHaveProperty("environment");
  });

  it("is not sent again on the next turn", async () => {
    mount();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Environment" }), "e1");
    await send("first");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole("combobox", { name: "Environment" })).toBeNull());
    await send("second");
    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalledTimes(2));
    expect(vi.mocked(streamCodeTurn).mock.calls[1][0]).not.toHaveProperty("environment");
  });
});
