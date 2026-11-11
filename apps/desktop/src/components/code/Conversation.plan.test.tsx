import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { streamCodeTurn, type CodeTurnHandlers } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

/**
 * The conversation's ⋮ menu offers the Plan panel only once the turn has proposed a plan. The plan
 * arrives as a `plan` frame; this drives a turn that sends one and checks the item appears.
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

async function sendAndOpenMenu() {
  const box = await screen.findByRole("textbox");
  await userEvent.type(box, "arruma o cabeçalho{Enter}");
  await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
  await userEvent.click(await screen.findByTestId("conversation-menu"));
  return within(await screen.findByRole("menu"));
}

describe("the conversation's Plan menu item", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset();
  });

  it("is absent while the turn has proposed no plan", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(async () => ({ turnId: "t1", seq: 1 }) as never);
    mount();
    const menu = await sendAndOpenMenu();
    expect(menu.queryByTestId("plan-panel-open")).toBeNull();
  });

  it("appears once the turn sends a plan frame", async () => {
    vi.mocked(streamCodeTurn).mockImplementation(async (_req: unknown, h: CodeTurnHandlers) => {
      h.onPlan?.({ steps: ["Ler src/a.ts", "Editar src/a.ts"], raw: "1. Ler src/a.ts\n2. Editar src/a.ts" });
      return { turnId: "t1", seq: 2 } as never;
    });
    mount();
    const menu = await sendAndOpenMenu();
    expect(menu.getByTestId("plan-panel-open")).toBeInTheDocument();
  });
});
