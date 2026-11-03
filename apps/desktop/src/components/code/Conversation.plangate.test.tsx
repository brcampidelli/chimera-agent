import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { streamCodeTurn } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The composer's plan-first choice: Auto, Always, or Never.
 *
 * The gate itself lives in `chimera/api/plan_gate.py` and is pinned there; what this file pins is
 * the part a person touches. Auto is the default and sends nothing, so the server's rule decides: a
 * big task plans first. Always and Never are the owner's own answers, and the rule does not override
 * them. A default that quietly became Always would stop every turn for a plan nobody asked for; one
 * that became Never would switch off the stop a big task gets. Both are pinned below.
 */
function mount() {
  renderWithProviders(
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

async function planGroup() {
  return within(await screen.findByRole("group", { name: /plan first/i }));
}

async function send(): Promise<{ plan_gate?: boolean }> {
  const box = await screen.findByRole("textbox");
  await userEvent.type(box, "arruma o cabeçalho{Enter}");
  await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
  return vi.mocked(streamCodeTurn).mock.calls[0]?.[0] as { plan_gate?: boolean };
}

describe("the plan-first choice is the owner's, with the rule as the default", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    localStorage.clear();
  });

  it("is Auto by default, and Auto omits the field so the rule decides", async () => {
    // Was `toMatchObject({ plan_gate: false })`. Auto must not send `false`: an explicit false would
    // switch off the server's rule that makes a big task plan first, and only the owner's own
    // choice ("Nunca") should do that.
    mount();
    const group = await planGroup();
    expect(group.getByRole("button", { name: "Auto" })).toHaveAttribute("aria-pressed", "true");
    expect(await send()).not.toHaveProperty("plan_gate");
  });

  it("Always sends plan_gate true", async () => {
    mount();
    const group = await planGroup();
    await userEvent.click(group.getByRole("button", { name: "Always" }));
    expect(group.getByRole("button", { name: "Always" })).toHaveAttribute("aria-pressed", "true");
    expect(await send()).toMatchObject({ plan_gate: true });
  });

  it("Never sends plan_gate false, the one way a person turns the rule off", async () => {
    mount();
    const group = await planGroup();
    await userEvent.click(group.getByRole("button", { name: "Never" }));
    expect(await send()).toMatchObject({ plan_gate: false });
  });
});
