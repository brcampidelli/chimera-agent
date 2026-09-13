import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { streamCodeTurn } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The composer's plan-gate toggle.
 *
 * The gate itself lives in `chimera/api/plan_gate.py` and is pinned there; what this file pins is
 * the part a person touches. Two properties, and the OFF one matters as much as the ON one: the
 * gate adds a model call and a wait to the most-used surface in the product, so a turn that did not
 * ask for it must not get it. A default that drifted to on would be discovered as "the app got slow
 * and keeps interrupting me", which is how a safety feature gets switched off for good.
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

async function send(): Promise<{ plan_gate?: boolean }> {
  const box = await screen.findByRole("textbox");
  await userEvent.type(box, "arruma o cabeçalho{Enter}");
  await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
  return vi.mocked(streamCodeTurn).mock.calls[0]?.[0] as { plan_gate?: boolean };
}

describe("the plan gate is a per-turn choice", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    localStorage.clear();
  });

  it("is OFF unless the turn asked for it", async () => {
    mount();
    expect(await send()).toMatchObject({ plan_gate: false });
  });

  it("arms the gate when the composer's toggle is pressed", async () => {
    mount();
    const toggle = await screen.findByRole("button", { name: /plan first/i });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(await send()).toMatchObject({ plan_gate: true });
  });
});
