/**
 * An in-memory `BroadcastChannel` for tests: what one instance posts reaches every OTHER open instance
 * of the same name, on a later microtask, and never the sender — the two properties the real one has
 * that the floating windows (dynamic screen, phase 7) rely on. Deterministic where the real one hops
 * through the event loop of another thread.
 */
export class FakeChannel {
  static open = new Set<FakeChannel>();
  onmessage: ((e: MessageEvent<unknown>) => void) | null = null;
  closed = false;

  constructor(readonly name: string) {
    FakeChannel.open.add(this);
  }

  postMessage(data: unknown): void {
    if (this.closed) throw new Error("posted on a closed channel");
    // A structured clone, like the real one: nothing the receiver holds is the sender's object.
    const copy: unknown = JSON.parse(JSON.stringify(data));
    for (const other of FakeChannel.open) {
      if (other === this || other.name !== this.name) continue;
      queueMicrotask(() => {
        if (!other.closed) other.onmessage?.(new MessageEvent("message", { data: copy }));
      });
    }
  }

  close(): void {
    this.closed = true;
    FakeChannel.open.delete(this);
  }

  static reset(): void {
    for (const c of FakeChannel.open) c.closed = true;
    FakeChannel.open.clear();
  }
}
