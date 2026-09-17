import { describe, expect, it, vi } from "vitest";

import { streamCodeTurn, type CodeBrowserFrame, type CodeTurnHandlers } from "@/lib/api";

/**
 * The browser frame, read off the wire exactly as `chimera/api/code_api.py` writes it.
 *
 * Same reason `approval-frame.test.ts` gives: the dispatcher is the one place a named SSE frame
 * becomes a handler call, and `payload as unknown as CodeBrowserFrame` is a cast, not a check. A
 * script that invoked `onBrowser` itself would agree with whatever this client believes and never
 * touch the wire — so the panel tests (Code.browser.test.tsx) prove the drawing, and this proves
 * the frame arrives with every field the panel reads.
 */

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

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

const SENT = {
  action: "navigate",
  url: "https://example.com/docs",
  title: "Example Domain",
  width: 1280,
  height: 720,
  jpeg: "/9j/4AAQSkZJRgABAQAAAQABAAD/stub",
  n: 1,
};

describe("the browser frame on a coding turn", () => {
  it("reaches onBrowser with every field the panel reads, and no other handler", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        stream([
          frame("session", { session_id: "s1", turn_id: "t1", seq: 1 }),
          frame("browser", { ...SENT, seq: 2 }),
          frame("done", { answer: "browsed", steps: 1, stopped_reason: "final", tool_names: ["browser"], model: "m", prompt_tokens: 1, completion_tokens: 1, usd: null, route_meta: null, context_peak_tokens: 0, seq: 3 }),
        ]),
      ),
    );
    const h = {
      onSession: vi.fn(),
      onBrowser: vi.fn(),
      onTool: vi.fn(),
      onDone: vi.fn(),
      onError: vi.fn(),
    } satisfies CodeTurnHandlers;

    await streamCodeTurn({ message: "open the docs" }, h);

    expect(h.onBrowser).toHaveBeenCalledTimes(1);
    const got = h.onBrowser.mock.calls[0][0] as CodeBrowserFrame;
    expect(got.action).toBe(SENT.action);
    expect(got.url).toBe(SENT.url);
    expect(got.title).toBe(SENT.title);
    expect(got.width).toBe(SENT.width);
    expect(got.height).toBe(SENT.height);
    expect(got.jpeg).toBe(SENT.jpeg);
    expect(got.n).toBe(SENT.n);
    expect(h.onTool).not.toHaveBeenCalled();
    expect(h.onDone).toHaveBeenCalledTimes(1);
    expect(h.onError).not.toHaveBeenCalled();
  });
});
