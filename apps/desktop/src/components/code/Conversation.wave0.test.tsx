import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Conversation, Verdict, fixBrief } from "@/components/code/Conversation";
import { streamCodeTurn } from "@/lib/api";
import { renderWithProviders } from "@/test/utils";

vi.mock("@/lib/api", async () => (await import("@/test/code-api-mock")).makeCodeApiMock());

function t(key: string, args?: Record<string, unknown>): string {
  return args?.cmd ? `${key}:${String(args.cmd)}` : key;
}

/**
 * Three things the Code screen was doing to every user, fixed together because they are the same
 * shape: a capability that existed, and a screen that did not reach for it.
 */
describe("what the turn asks for", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
    localStorage.clear();
  });

  it("sends a step ceiling instead of taking the library's 8", async () => {
    // `AgentConfig.max_steps` is 8, and this screen sent nothing — so the app shipped on the
    // configuration `bench/swe_bench/RESULTS.md` run 1 published as +0.0% lift and called
    // "a starved agent". Every positive result the project has was measured at 30.
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

    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "arruma o cabeçalho{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    const enviado = vi.mocked(streamCodeTurn).mock.calls[0]?.[0] as { max_steps?: number };
    expect(enviado.max_steps).toBeGreaterThanOrEqual(30);
    expect(enviado.max_steps).toBeLessThanOrEqual(100); // the server clamps here
  });
});

describe("what the fix button carries", () => {
  const v = {
    state: "failed" as const,
    command: "pytest -q",
    source: "inferred:pyproject.toml",
    output: "E   assert 3 == 4\nE   AssertionError",
    revert_token: null,
  };

  it("carries the failure, not just the original request", () => {
    // It used to re-send the user's message and nothing else: the same words that produced the
    // failure, with no mention of the failure — while the traceback was rendered on screen
    // directly above the button.
    const texto = fixBrief("faz a soma dar 4", v as never, t as never);

    expect(texto).toContain("assert 3 == 4");
    expect(texto).toContain("pytest -q");
    expect(texto).toContain("faz a soma dar 4");
  });

  it("keeps the END of a long output, where the assertion is", () => {
    const enorme = { ...v, output: "ruido\n".repeat(5000) + "E   the real failure" };
    const texto = fixBrief("x", enorme as never, t as never);

    expect(texto).toContain("E   the real failure");
    expect(texto.length).toBeLessThan(4600);
  });

  it("falls back to the original when there is nothing to add", () => {
    // The control: a verdict with no command and no output must behave exactly as before, rather
    // than wrapping the message in empty scaffolding.
    const vazio = { ...v, command: null, output: null };
    expect(fixBrief("faz a soma dar 4", vazio as never, t as never)).toBe("faz a soma dar 4");
  });
});

describe("the fix button is wired to the brief, not just to the message", () => {
  /**
   * The test the first version of this file would have survived.
   *
   * `fixBrief` had three tests and all three passed with the button still sending `original` —
   * because they tested the FUNCTION and the defect was in the WIRING. That is the same shape as
   * the `deliver_to` field this project shipped for months: a mechanism nobody called.
   */
  it("hands over the traceback when clicked", async () => {
    const recebido: string[] = [];
    renderWithProviders(
      <Verdict
        v={
          {
            state: "failed",
            command: "pytest -q",
            source: "inferred:pyproject.toml",
            output: "E   assert 3 == 4",
            revert_token: null,
          } as never
        }
        original="faz a soma dar 4"
        onUndo={() => {}}
        onFix={(text) => recebido.push(text)}
        t={((k: string) => k) as never}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /verdict\.fix/ }));

    expect(recebido).toHaveLength(1);
    expect(recebido[0]).toContain("assert 3 == 4");
    expect(recebido[0]).toContain("faz a soma dar 4");
  });
});

describe("the empty screen", () => {
  beforeEach(() => {
    vi.mocked(streamCodeTurn).mockReset().mockResolvedValue(undefined as never);
  });

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

  it("puts the cursor in the box", async () => {
    // The first thing anyone does on this screen is type. Making them click the box first is a
    // step that exists only because nobody removed it.
    mount();
    const box = await screen.findByRole("textbox");
    expect(document.activeElement).toBe(box);
  });

  it("offers examples, and clicking one FILLS the box rather than sending", async () => {
    // An empty field asks the user to already know what to ask for. The examples show the shape of
    // a good first message — and they fill rather than send, because the first thing someone does
    // with an example is edit it, and spending money before they have read it is a worse
    // introduction than no examples at all.
    mount();
    const exemplos = await screen.findAllByRole("button", { name: /explain|contact form|tests fail/i });
    expect(exemplos.length).toBeGreaterThanOrEqual(3);

    await userEvent.click(exemplos[0]);

    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBeTruthy();
    expect(streamCodeTurn).not.toHaveBeenCalled();
  });

  it("arms a spend ceiling before the first message", async () => {
    // The step ceiling this screen sends went from the library's 8 to 40, in an app other people
    // install. A first message should not be able to cost whatever a loop feels like, and a limit
    // nobody can see is a limit nobody can raise — so it is armed AND in the box.
    mount();
    const box = await screen.findByRole("textbox");
    await userEvent.type(box, "arruma o cabeçalho{Enter}");

    await waitFor(() => expect(streamCodeTurn).toHaveBeenCalled());
    const enviado = vi.mocked(streamCodeTurn).mock.calls[0]?.[0] as { max_usd?: number };
    expect(enviado.max_usd).toBeGreaterThan(0);
  });
});
