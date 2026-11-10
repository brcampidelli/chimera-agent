import { act, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Conversation } from "@/components/code/Conversation";
import { useHotkeys } from "@/lib/hotkeys";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function Help(props: { onHelp: () => void }) {
  useHotkeys({
    onPalette: vi.fn(),
    onSettings: vi.fn(),
    onNewChat: vi.fn(),
    onNavigate: vi.fn(),
    onHelp: props.onHelp,
  });
  return null;
}

function mount(resumeSession: string | null, onHelp: () => void = vi.fn()) {
  return renderWithProviders(
    <>
      <Help onHelp={onHelp} />
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
        resumeSession={resumeSession}
      />
    </>,
  );
}

/** Ctrl+; and Ctrl+/ on the window, as the keyboard sends them. */
function chord(key: string) {
  act(() => {
    fireEvent.keyDown(window, { key, code: key === ";" ? "Semicolon" : "Slash", ctrlKey: true });
  });
}

describe("the side question, with the Code conversation mounted", () => {
  it("a brand-new conversation has nothing to ask about, and says so", async () => {
    mount(null);

    chord(";");

    await waitFor(() => expect(screen.getByTestId("side-chat")).toBeInTheDocument());
    expect(screen.queryByRole("textbox", { name: /pergunte|ask about/i })).not.toBeInTheDocument();
    expect(screen.getByText(/native model|modelo nativo|nativen Modell|modèle natif|modello nativo/i)).toBeInTheDocument();
  });

  it("an existing conversation opens the panel with its field, and Ctrl+; closes it again", async () => {
    mount("s9");

    chord(";");
    await waitFor(() => expect(screen.getByTestId("side-chat")).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: /pergunte|ask about/i })).toBeInTheDocument();

    chord(";");
    await waitFor(() => expect(screen.queryByTestId("side-chat")).not.toBeInTheDocument());
  });

  it("Ctrl+/ opens the keyboard shortcuts sheet, and does not open the side question", () => {
    const onHelp = vi.fn();
    mount("s9", onHelp);

    chord("/");

    expect(onHelp).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("side-chat")).not.toBeInTheDocument();
  });

  it("Ctrl+; opens the side question and never the shortcuts sheet", () => {
    const onHelp = vi.fn();
    mount("s9", onHelp);

    chord(";");

    expect(screen.getByTestId("side-chat")).toBeInTheDocument();
    expect(onHelp).not.toHaveBeenCalled();
  });
});
