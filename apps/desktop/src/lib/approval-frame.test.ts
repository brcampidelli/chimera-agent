import { describe, expect, it, vi } from "vitest";

import { streamCodeTurn, type CodeApprovalEvent, type CodeTurnHandlers } from "@/lib/api";

/**
 * The frame that parks a turn on a person, read off the wire exactly as the server writes it.
 *
 * `CodeApprovalEvent` is the shape the API and this client agreed on, and the dispatcher is the one
 * place that turns a named SSE frame into a handler call. Everything downstream — the card, the
 * countdown, the two buttons — is written against that shape, so a field renamed on one side and not
 * the other produces no error anywhere: `payload as unknown as CodeApprovalEvent` is a cast, and a
 * cast asserts rather than checks. The turn would simply park on a question the screen draws with
 * `undefined` in it, which is the failure the whole surface exists to prevent, arriving silently.
 *
 * So this reads a canned stream rather than calling the handlers directly. A test that invoked
 * `onApproval` itself would agree with whatever the client believes and never touch the wire.
 *
 * The payload below is copied from `chimera/api/code_api.py` — `id`, `action`, `reason`, `asked_at`,
 * `wait_seconds`, plus the `seq` every frame carries.
 */

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

/** A response whose body hands over the given SSE chunks and then closes cleanly. */
function stream(chunks: string[]): Response {
  const enc = new TextEncoder();
  let i = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(enc.encode(chunks[i++]));
      else controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
}

const ASKED = {
  id: "q-7f3a",
  action: "write_file(path='notes.md')",
  reason: "write_file is restricted after this run consumed untrusted content",
  asked_at: 1_757_500_000.5,
  wait_seconds: 300.0,
};

function handlers() {
  return {
    onSession: vi.fn(),
    onToken: vi.fn(),
    onApproval: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
  } satisfies CodeTurnHandlers;
}

describe("the approval frame on a coding turn", () => {
  it("reaches onApproval with every field the card reads, and no other handler", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        stream([
          frame("session", { session_id: "s1", turn_id: "t1", seq: 1 }),
          frame("token", { text: "Let me write that down.", seq: 2 }),
          frame("approval", { ...ASKED, seq: 3 }),
        ]),
      ),
    );
    const h = handlers();

    await streamCodeTurn({ message: "write it to notes.md" }, h);

    expect(h.onApproval).toHaveBeenCalledTimes(1);
    const got = h.onApproval.mock.calls[0][0] as CodeApprovalEvent;
    // Field by field rather than a shape match: the point is that each one SURVIVED the wire, and
    // `toMatchObject` on a cast object passes just as happily with a field missing.
    expect(got.id).toBe(ASKED.id);
    expect(got.action).toBe(ASKED.action);
    expect(got.reason).toBe(ASKED.reason);
    expect(got.asked_at).toBe(ASKED.asked_at);
    expect(got.wait_seconds).toBe(ASKED.wait_seconds);
    // A parked turn is not a finished one, and not a broken one.
    expect(h.onDone).not.toHaveBeenCalled();
    expect(h.onError).not.toHaveBeenCalled();
  });

  it("survives the replay path, and is not delivered twice", async () => {
    // A dropped connection is exactly when a question is easiest to lose: the frame was sent, the
    // client never read it, and the turn is still blocked on an answer nobody can see. The resume
    // carries the same frame with its `seq`, and the `seen` guard is what stops a client that DID
    // read it drawing a second card for one question.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) => {
        if (String(url).includes("/api/code/turns/")) {
          return new Response(
            JSON.stringify({
              turn_id: "t1",
              frames: [
                { event: "token", text: "Let me write that down.", seq: 2 },
                { event: "approval", ...ASKED, seq: 3 },
              ],
              seq: 3,
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          );
        }
        const enc = new TextEncoder();
        let sent = false;
        const body = new ReadableStream<Uint8Array>({
          pull(controller) {
            if (!sent) {
              sent = true;
              controller.enqueue(
                enc.encode(frame("session", { session_id: "s1", turn_id: "t1", seq: 1 })),
              );
              return;
            }
            controller.error(new Error("network error"));
          },
        });
        return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
      }),
    );
    const h = handlers();

    await streamCodeTurn({ message: "write it to notes.md" }, h);

    expect(h.onApproval).toHaveBeenCalledTimes(1);
    expect((h.onApproval.mock.calls[0][0] as CodeApprovalEvent).id).toBe(ASKED.id);
  });
});
