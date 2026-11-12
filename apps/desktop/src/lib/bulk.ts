/** What a bulk run did: the items that went through and the ones that did not. */
export interface BulkResult<T> {
  ok: T[];
  failed: T[];
}

/**
 * Apply `work` to every item, and keep going when one fails.
 *
 * A bulk action on forty folders must not stop at the third that the server refuses, and must not
 * pretend the rest were fine either, so the failures are returned by name for the caller to count.
 * `concurrency` is small on purpose: the folder registry is one file the server rewrites on every
 * write, so those calls run one at a time; conversations are independent files and may overlap.
 */
export async function runBulk<T>(
  items: readonly T[],
  work: (item: T) => Promise<unknown>,
  options: { concurrency?: number; onProgress?: (done: number, total: number) => void } = {},
): Promise<BulkResult<T>> {
  const { concurrency = 1, onProgress } = options;
  const result: BulkResult<T> = { ok: [], failed: [] };
  let next = 0;
  let done = 0;
  async function worker() {
    while (next < items.length) {
      const item = items[next++];
      try {
        await work(item);
        result.ok.push(item);
      } catch {
        result.failed.push(item);
      }
      done += 1;
      onProgress?.(done, items.length);
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, Math.min(concurrency, items.length)) }, worker));
  return result;
}
