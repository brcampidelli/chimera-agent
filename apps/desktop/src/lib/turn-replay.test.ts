import { describe, expect, it, vi } from "vitest";

import { streamCodeTurn, type CodeTurnHandlers } from "@/lib/api";

/**
 * A dropped connection threw away work that was still running, and still being paid for.
 *
 * The server keeps every frame of a coding turn now, numbered, and answers "what came after N".
 * This is the half that uses it: the client tracks the turn's id and how far it read, and on a cut
 * asks once for the rest.
 *
 * Once, not in a loop — a resume that can itself resume is a poll wearing a recovery's clothes. And
 * the `seq` guard is what makes it safe: a frame already applied is dropped, so replay-then-live
 * and live-only land on the same state rather than on a doubled answer.
 */

function frame(evento: string, dados: unknown): string {
  return `event: ${evento}\ndata: ${JSON.stringify(dados)}\n\n`;
}

function corta(pedacos: string[], erro?: Error): Response {
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

type Espiadas = {
  onSession: ReturnType<typeof vi.fn>;
  onToken: ReturnType<typeof vi.fn>;
  onDone: ReturnType<typeof vi.fn>;
  onError: ReturnType<typeof vi.fn>;
};

function handlers(): CodeTurnHandlers & Espiadas {
  return {
    onSession: vi.fn(),
    onToken: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  } as CodeTurnHandlers & Espiadas;
}

/** A stream that dies after two tokens, and a replay endpoint holding the rest. */
function cenario(resto: unknown[], { ok = true } = {}) {
  const chamadas: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string | URL) => {
      const alvo = String(url);
      chamadas.push(alvo);
      if (alvo.includes("/api/code/turns/")) {
        return new Response(JSON.stringify({ turn_id: "t1", frames: resto, seq: 4 }), {
          status: ok ? 200 : 500,
          headers: { "content-type": "application/json" },
        });
      }
      return corta(
        [
          frame("session", { session_id: "s1", turn_id: "t1" }),
          frame("token", { text: "Olá", seq: 1 }),
          frame("token", { text: " mundo", seq: 2 }),
        ],
        new Error("network error"),
      );
    }),
  );
  return chamadas;
}

describe("a coding turn whose stream was cut", () => {
  it("asks for what it missed", async () => {
    const chamadas = cenario([
      { event: "token", text: "!", seq: 3 },
      { event: "done", answer: "Olá mundo!", seq: 4 },
    ]);
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(chamadas.some((u) => u.includes("/api/code/turns/t1?since=2"))).toBe(true);
    expect(h.onDone).toHaveBeenCalled();
  });

  it("does not replay what it already showed", async () => {
    // The guard the whole design turns on. The server may hand back a frame the client already
    // applied — a resume from a cursor one behind, a retried request — and rendering "Olá" twice
    // is a worse failure than the drop, because it looks like the model said it twice.
    const chamadas = cenario([
      { event: "token", text: "Olá", seq: 1 },
      { event: "token", text: " mundo", seq: 2 },
      { event: "token", text: "!", seq: 3 },
    ]);
    void chamadas;
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(h.onToken.mock.calls.map((c: unknown[]) => c[0])).toEqual(["Olá", " mundo", "!"]);
  });

  it("reports the cut when the replay itself fails", async () => {
    // Once only. If the second read fails too, the error is the honest answer — a client that kept
    // asking would spin against a server that is gone.
    cenario([], { ok: false });
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(h.onError).toHaveBeenCalled();
  });

  it("reports the cut when there is no turn to ask about", async () => {
    // A stream that died before its first frame has no id, so there is nothing to resume from and
    // pretending otherwise would send a request to `/api/code/turns/`.
    vi.stubGlobal("fetch", vi.fn(async () => corta([], new Error("gone"))));
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(h.onError).toHaveBeenCalled();
  });
});

describe("a coding turn that finished", () => {
  it("never asks for a replay", async () => {
    // The guard against a client that quietly makes a second request on every successful turn.
    const chamadas: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) => {
        chamadas.push(String(url));
        return corta([
          frame("session", { session_id: "s1", turn_id: "t1" }),
          frame("token", { text: "pronto", seq: 1 }),
          frame("done", { answer: "pronto", seq: 2 }),
        ]);
      }),
    );
    const h = handlers();

    await streamCodeTurn({ message: "oi" }, h);

    expect(chamadas.filter((u) => u.includes("/api/code/turns/"))).toEqual([]);
    expect(h.onError).not.toHaveBeenCalled();
  });
});
