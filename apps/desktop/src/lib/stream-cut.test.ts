import { describe, expect, it, vi } from "vitest";

import { streamCodeTurn, type CodeTurnHandlers } from "@/lib/api";

/**
 * A dropped connection ended the stream exactly the way a finished turn does.
 *
 * Eight loops read `{ value, done }` inline with no `try` around the read and no check afterwards.
 * A connection cut mid-stream either threw an unhandled rejection or — the common case — surfaced
 * as `done: true`, so the loop exited on the same branch a clean finish takes. `onError` never
 * fired, and the screen kept showing a turn that was still in progress on a server that had gone
 * away.
 *
 * This is the half that gives replay a trigger: nothing can recover from a drop it cannot see.
 */

function respostaQueCorta(pedacos: string[], erro?: Error): Response {
  // One chunk per `pull`, with the error on a LATER pull rather than in `start`. Written the other
  // way first — enqueue everything, then `controller.error()` — and the queued chunks are discarded
  // by the stream, so the test asserted that a reader keeps what arrived while making sure nothing
  // ever arrived.
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < pedacos.length) {
        controller.enqueue(enc.encode(pedacos[i++]));
        return;
      }
      if (erro) controller.error(erro);
      else controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

function quadro(evento: string, dados: unknown): string {
  return `event: ${evento}\ndata: ${JSON.stringify(dados)}\n\n`;
}

function handlers(): CodeTurnHandlers & { onError: ReturnType<typeof vi.fn> } {
  return {
    onToken: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  } as CodeTurnHandlers & { onError: ReturnType<typeof vi.fn> };
}

describe("a stream that was cut", () => {
  it("reports an error instead of ending quietly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      respostaQueCorta([quadro("token", { text: "Olá" })], new Error("network error")),
    ));
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(h.onError).toHaveBeenCalled();
    expect(String(h.onError.mock.calls[0][0])).toContain("network");
  });

  it("keeps what already arrived", async () => {
    // A cut is not a reason to discard the tokens the reader already saw — they were paid for and
    // they are on screen.
    vi.stubGlobal("fetch", vi.fn(async () =>
      respostaQueCorta([quadro("token", { text: "Olá" })], new Error("boom")),
    ));
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(h.onToken).toHaveBeenCalledWith("Olá");
  });
});

describe("a stream that finished", () => {
  it("does not report an error", async () => {
    // The guard against a fix that calls every completed turn a failure — which would be the same
    // defect with the sign flipped, and far more visible.
    vi.stubGlobal("fetch", vi.fn(async () =>
      respostaQueCorta([quadro("token", { text: "pronto" }), quadro("done", { answer: "pronto" })]),
    ));
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(h.onDone).toHaveBeenCalled();
    expect(h.onError).not.toHaveBeenCalled();
  });

  it("still delivers a frame with no trailing blank line", async () => {
    // The last frame of a stream often arrives without its separator, and the old loop flushed the
    // buffer after the read. That behaviour has to survive being moved into the helper.
    vi.stubGlobal("fetch", vi.fn(async () =>
      respostaQueCorta([`event: done\ndata: ${JSON.stringify({ answer: "fim" })}`]),
    ));
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(h.onDone).toHaveBeenCalled();
  });
});
