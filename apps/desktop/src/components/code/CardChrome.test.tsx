import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { CardChrome, cardId, useCardModes, type CardModes } from "@/components/code/CardChrome";
import { ToastProvider } from "@/components/ui/toast";
import type { CardKind } from "@/lib/layout/model";
import { STORAGE_KEY } from "@/lib/layout/store";
import { renderWithProviders } from "@/test/utils";

/**
 * Phase 3 of the dynamic screen: every card of the conversation can be minimised to one line, closed
 * (only that card, in that turn, with an Undo and a chip that brings it back), or have its whole kind
 * minimised from now on. Approval, spend warnings and a failed turn's error minimise and never close.
 */
let modes: CardModes | null = null;

function Turn({ kinds, turn = 0 }: { kinds: CardKind[]; turn?: number }) {
  const cards = useCardModes();
  modes = cards;
  return (
    <div>
      {kinds.map((kind, n) => (
        <CardChrome key={`${kind}${n}`} id={cardId(turn, kind, n)} kind={kind} cards={cards}>
          <p>{`${kind} body ${n}`}</p>
        </CardChrome>
      ))}
      {cards.closedInTurn(turn) > 0 ? (
        <button type="button" onClick={() => cards.showTurn(turn)}>
          {`hidden ${cards.closedInTurn(turn)}`}
        </button>
      ) : null}
    </div>
  );
}

function mount(kinds: CardKind[]) {
  // The toast provider is the app's (`main.tsx`); the shared helper leaves it out on purpose, because
  // its always-present status region would change what "renders nothing" means in every other suite.
  return renderWithProviders(
    <ToastProvider>
      <Turn kinds={kinds} />
    </ToastProvider>,
  );
}

describe("CardChrome", () => {
  beforeEach(() => {
    localStorage.clear();
    modes = null;
  });

  it("minimises a card to one line and expands it again", async () => {
    const user = userEvent.setup();
    mount(["tools"]);

    await user.click(screen.getByRole("button", { name: "Minimize Tools" }));
    expect(screen.queryByText("tools body 0")).not.toBeInTheDocument();
    expect(screen.getByText("Tools")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Expand Tools" }));
    expect(screen.getByText("tools body 0")).toBeInTheDocument();
  });

  it("closes only that card, says so with an Undo, and Undo brings it back", async () => {
    const user = userEvent.setup();
    mount(["diff", "diff"]);

    const [first] = screen.getAllByRole("button", { name: "Close Changes" });
    await user.click(first);

    expect(screen.queryByText("diff body 0")).not.toBeInTheDocument();
    expect(screen.getByText("diff body 1")).toBeInTheDocument();
    expect(screen.getByText("Changes closed")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(screen.getByText("diff body 0")).toBeInTheDocument();
  });

  it("counts what was closed in the turn, and the chip brings all of it back", async () => {
    const user = userEvent.setup();
    mount(["tools", "receipt"]);

    await user.click(screen.getByRole("button", { name: "Close Tools" }));
    await user.click(screen.getByRole("button", { name: "Close Receipt" }));
    await user.click(screen.getByRole("button", { name: "hidden 2" }));

    expect(screen.getByText("tools body 0")).toBeInTheDocument();
    expect(screen.getByText("receipt body 1")).toBeInTheDocument();
  });

  it.each([
    ["approval", "Approval", "An approval is your decision: it minimizes but never closes."],
    ["notices", "Warnings", "Spend and limit warnings minimize but never close."],
    ["error", "Error", "A failed turn's error minimizes but never closes."],
  ] as const)("never closes %s, and the disabled button says why", async (kind, name, why) => {
    const user = userEvent.setup();
    mount([kind]);

    const close = screen.getByRole("button", { name: `Close ${name}` });
    expect(close).toHaveAttribute("aria-disabled", "true");
    await user.hover(close);
    expect((await screen.findAllByText(why)).length).toBeGreaterThan(0);
    await user.click(close);
    expect(screen.getByText(`${kind} body 0`)).toBeInTheDocument();

    // And a caller that asks anyway is refused too: the rule is not only the button's.
    act(() => modes?.set(cardId(0, kind), kind, "closed"));
    expect(screen.getByText(`${kind} body 0`)).toBeInTheDocument();
  });

  it("minimises a whole kind from now on, keeps it in the layout, and a per-card choice yields to it", async () => {
    const user = userEvent.setup();
    mount(["tools", "tools", "receipt"]);
    // One tools card expanded explicitly first; the kind-wide choice then speaks for it too.
    await user.click(screen.getAllByRole("button", { name: "Minimize Tools" })[0]);
    await user.click(screen.getAllByRole("button", { name: "Expand Tools" })[0]);

    await user.click(screen.getAllByRole("button", { name: "Always minimize Tools" })[0]);

    expect(screen.queryByText("tools body 0")).not.toBeInTheDocument();
    expect(screen.queryByText("tools body 1")).not.toBeInTheDocument();
    expect(screen.getByText("receipt body 2")).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}").cards.tools).toBe("minimized");
  });

  it("opens a new approval whatever the last one was left as", async () => {
    const user = userEvent.setup();
    const { rerender } = mount(["approval"]);
    await user.click(screen.getByRole("button", { name: "Minimize Approval" }));
    expect(screen.queryByText("approval body 0")).not.toBeInTheDocument();

    // The same screen, a different question: a new id.
    rerender(
      <ToastProvider>
        <Turn kinds={["approval"]} turn={1} />
      </ToastProvider>,
    );
    expect(screen.getByText("approval body 0")).toBeInTheDocument();
  });

  it("does not store which cards were closed: reopening the screen shows them", async () => {
    const user = userEvent.setup();
    const first = mount(["verdict"]);
    await user.click(screen.getByRole("button", { name: "Close Verification" }));
    first.unmount();

    mount(["verdict"]);
    expect(screen.getByText("verdict body 0")).toBeInTheDocument();
  });

  it("puts the controls on the open card and on its minimised line alike", async () => {
    const user = userEvent.setup();
    mount(["browser"]);
    const card = screen.getByText("browser body 0").closest("[data-card]") as HTMLElement;
    expect(within(card).getByRole("button", { name: "Close Browser" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Minimize Browser" }));
    const line = screen.getByText("Browser").closest("[data-card]") as HTMLElement;
    expect(within(line).getByRole("button", { name: "Close Browser" })).toBeInTheDocument();
  });
});
