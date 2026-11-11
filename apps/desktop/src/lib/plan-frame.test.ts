import { describe, expect, it, vi } from "vitest";

import { streamCodeTurn } from "@/lib/api";

/**
 * The `plan` frame the plan gate writes before it asks (`chimera/api/code_api.py`, `on_plan`), read off
 * a canned stream. The payload is `{steps, raw}`; the Plan panel draws the steps, so a dispatcher that
 * dropped them would show an empty plan with a live approval button under it.
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

describe("the plan frame on a coding turn", () => {
  it("reaches onPlan with the steps in order, and nothing else", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        stream([
          frame("session", { session_id: "s1", turn_id: "t1", seq: 1 }),
          frame("plan", { steps: ["Ler src/a.ts", "Editar src/a.ts"], raw: "1. Ler src/a.ts\n2. Editar src/a.ts", seq: 2 }),
        ]),
      ),
    );
    const onPlan = vi.fn();
    const onApproval = vi.fn();

    await streamCodeTurn({ message: "mude a.ts" }, { onSession: vi.fn(), onPlan, onApproval });

    expect(onPlan).toHaveBeenCalledTimes(1);
    expect(onPlan.mock.calls[0][0]).toEqual({ steps: ["Ler src/a.ts", "Editar src/a.ts"], raw: "1. Ler src/a.ts\n2. Editar src/a.ts" });
    expect(onApproval).not.toHaveBeenCalled();
  });
});
