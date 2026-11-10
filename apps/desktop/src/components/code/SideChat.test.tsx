import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SideChat, useSideChatKey } from "@/components/code/SideChat";
import type { SideChatDone } from "@/lib/api";
import { I18nProvider } from "@/lib/i18n";

const { streamSideChat } = vi.hoisted(() => ({
  streamSideChat: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ streamSideChat }));

interface Props {
  sessionId?: string;
  model?: string;
  open?: boolean;
  unavailable?: boolean;
  onTake?: (text: string) => void;
  onClose?: () => void;
}

function mount(props: Props = {}) {
  const onTakeSpy = vi.fn<(text: string) => void>();
  const onTake = props.onTake ?? onTakeSpy;
  const onClose = props.onClose ?? vi.fn();
  const view = render(
    <I18nProvider>
      <SideChat
        sessionId={props.sessionId ?? "s1"}
        model={props.model ?? "vendor/model"}
        open={props.open ?? true}
        onClose={onClose}
        onTake={onTake}
        unavailable={props.unavailable ?? false}
      />
    </I18nProvider>,
  );
  return { ...view, onTake: onTakeSpy, onClose, user: userEvent.setup() };
}

/** A fake stream: the server's frames, delivered in order. */
function answerWith(
  tokens: string[],
  done: SideChatDone = { model: "vendor/model", prompt_tokens: 10, completion_tokens: 3, usd: 0.0012 },
) {
  streamSideChat.mockImplementation(
    async (
      _session: string,
      _req: { message: string; model?: string },
      handlers: { onToken?: (t: string) => void; onDone?: (d: SideChatDone) => void },
    ) => {
      for (const text of tokens) handlers.onToken?.(text);
      handlers.onDone?.(done);
    },
  );
}

describe("SideChat — a question beside the conversation", () => {
  beforeEach(() => {
    streamSideChat.mockReset();
  });

  it("asks the server with the conversation's model and shows the streamed answer", async () => {
    answerWith(["A tabela ", "chama-se pedidos."]);
    const { user } = mount();

    await user.type(screen.getByRole("textbox"), "qual tabela?{Enter}");

    await waitFor(() => expect(screen.getByText("A tabela chama-se pedidos.")).toBeInTheDocument());
    expect(streamSideChat).toHaveBeenCalledWith(
      "s1",
      { message: "qual tabela?", model: "vendor/model" },
      expect.any(Object),
      expect.any(AbortSignal),
    );
  });

  it("never hands the answer to the conversation on its own", async () => {
    answerWith(["resposta"]);
    const { user, onTake } = mount();

    await user.type(screen.getByRole("textbox"), "pergunta{Enter}");

    await waitFor(() => expect(screen.getByText("resposta")).toBeInTheDocument());
    expect(onTake).not.toHaveBeenCalled();
  });

  it("takes an answer to the composer only when the person presses the button", async () => {
    answerWith(["texto para levar"]);
    const { user, onTake } = mount();
    await user.type(screen.getByRole("textbox"), "x{Enter}");
    await waitFor(() => expect(screen.getByText("texto para levar")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /levar para a conversa|take to the conversation/i }));

    expect(onTake).toHaveBeenCalledExactlyOnceWith("texto para levar");
  });

  it("shows what the answer cost, and says so when the price is unknown", async () => {
    answerWith(["ok"], { model: "vendor/model", prompt_tokens: 10, completion_tokens: 3, usd: null });
    const { user } = mount();
    await user.type(screen.getByRole("textbox"), "x{Enter}");

    await waitFor(() => expect(screen.getByText(/13 tokens/)).toBeInTheDocument());
    expect(screen.getByText(/price unknown|preço desconhecido|unknown/i)).toBeInTheDocument();
  });

  it("shows the server's error in place of an answer and does not stay pending", async () => {
    streamSideChat.mockImplementation(async (_s: string, _r: unknown, handlers: { onError?: (m: string) => void }) => {
      handlers.onError?.("No provider key configured.");
    });
    const { user } = mount();

    await user.type(screen.getByRole("textbox"), "x{Enter}");

    await waitFor(() => expect(screen.getByText("No provider key configured.")).toBeInTheDocument());
  });

  it("names the one thing that cannot be done when the conversation is an external agent", () => {
    mount({ unavailable: true });

    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.getByText(/external agent|agente externo|Ein externer Agent/i)).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const { user, onClose } = mount();

    await user.click(screen.getByRole("textbox"));
    await user.keyboard("{Escape}");

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("starts an empty panel when the conversation changes", async () => {
    answerWith(["da conversa um"]);
    const first = mount({ sessionId: "one" });
    await first.user.type(screen.getByRole("textbox"), "x{Enter}");
    await waitFor(() => expect(screen.getByText("da conversa um")).toBeInTheDocument());

    first.rerender(
      <I18nProvider>
        <SideChat sessionId="two" model="" open onClose={vi.fn()} onTake={vi.fn()} unavailable={false} />
      </I18nProvider>,
    );

    await waitFor(() => expect(screen.queryByText("da conversa um")).not.toBeInTheDocument());
  });
});

describe("SideChat — taint, stopping, and taking an answer", () => {
  beforeEach(() => {
    streamSideChat.mockReset();
  });

  it("marks a tainted answer and asks before taking it, quoted under its label", async () => {
    streamSideChat.mockImplementation(
      async (_s: string, _r: unknown, handlers: { onStart?: (x: unknown) => void; onToken?: (t: string) => void; onDone?: (d: SideChatDone) => void }) => {
        handlers.onStart?.({ tainted: true, model: "vendor/model" });
        handlers.onToken?.("linha um\nlinha dois");
        handlers.onDone?.({ model: "vendor/model", prompt_tokens: 1, completion_tokens: 1, usd: 0 });
      },
    );
    const { user, onTake } = mount();
    await user.type(screen.getByRole("textbox"), "x{Enter}");
    await waitFor(() => expect(screen.getByText(/linha um/)).toBeInTheDocument());
    expect(screen.getByText(/conteúdo não confiável|untrusted content/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /levar para a conversa|take to the conversation/i }));
    // The first press asks; nothing reaches the composer yet.
    expect(onTake).not.toHaveBeenCalled();
    expect(screen.getByText(/como citação|as a quote/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /levar$|^take it$|^sim$|^confirm/i }));
    expect(onTake).toHaveBeenCalledOnce();
    const text = onTake.mock.calls[0][0] as string;
    expect(text).toMatch(/resposta lateral — dado, não instrução|side answer — data, not instruction/);
    expect(text).toContain("> linha um\n> linha dois");
  });

  it("takes a clean answer on one press, unquoted", async () => {
    streamSideChat.mockImplementation(
      async (_s: string, _r: unknown, handlers: { onStart?: (x: unknown) => void; onToken?: (t: string) => void; onDone?: (d: SideChatDone) => void }) => {
        handlers.onStart?.({ tainted: false, model: "vendor/model" });
        handlers.onToken?.("limpo");
        handlers.onDone?.({ model: "vendor/model", prompt_tokens: 1, completion_tokens: 1, usd: 0 });
      },
    );
    const { user, onTake } = mount();
    await user.type(screen.getByRole("textbox"), "x{Enter}");
    await waitFor(() => expect(screen.getByText("limpo")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /levar para a conversa|take to the conversation/i }));

    expect(onTake).toHaveBeenCalledWith("limpo");
  });

  it("stops reading when the panel closes, and says the call may finish in the background", async () => {
    let signalSeen: AbortSignal | undefined;
    streamSideChat.mockImplementation(
      (_s: string, _r: unknown, handlers: { onToken?: (t: string) => void }, signal?: AbortSignal) =>
        new Promise<void>((resolve) => {
          signalSeen = signal;
          handlers.onToken?.("começou");
          signal?.addEventListener("abort", () => resolve());
        }),
    );
    const onClose = vi.fn();
    const first = mount({ onClose });
    await first.user.type(screen.getByRole("textbox"), "x{Enter}");
    await waitFor(() => expect(screen.getByText("começou")).toBeInTheDocument());

    first.rerender(
      <I18nProvider>
        <SideChat sessionId="s1" model="vendor/model" open={false} onClose={onClose} onTake={vi.fn()} unavailable={false} />
      </I18nProvider>,
    );

    expect(signalSeen?.aborted).toBe(true);
    first.rerender(
      <I18nProvider>
        <SideChat sessionId="s1" model="vendor/model" open onClose={onClose} onTake={vi.fn()} unavailable={false} />
      </I18nProvider>,
    );
    expect(screen.getByText(/segundo plano|background/)).toBeInTheDocument();
  });

  it("shows the refusal of a second question while one runs", async () => {
    streamSideChat.mockImplementation(async (_s: string, _r: unknown, handlers: { onError?: (m: string) => void }) => {
      handlers.onError?.("a side question is already running here");
    });
    const { user } = mount();
    await user.type(screen.getByRole("textbox"), "x{Enter}");

    await waitFor(() => expect(screen.getByText("a side question is already running here")).toBeInTheDocument());
  });
});

describe("useSideChatKey — Mod+; opens and closes the panel", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    useSideChatKey(() => setOpen((value) => !value));
    return <span data-testid="state">{open ? "open" : "closed"}</span>;
  }

  it("toggles on the chord, even while the person is typing in the composer", async () => {
    const user = userEvent.setup();
    render(
      <>
        <textarea aria-label="composer" />
        <Harness />
      </>,
    );
    expect(screen.getByTestId("state")).toHaveTextContent("closed");

    await user.click(screen.getByLabelText("composer"));
    await user.keyboard("{Control>};{/Control}");
    expect(screen.getByTestId("state")).toHaveTextContent("open");

    await user.keyboard("{Control>};{/Control}");
    expect(screen.getByTestId("state")).toHaveTextContent("closed");
  });
});
