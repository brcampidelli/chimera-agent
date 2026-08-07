/** Read a message as a list of separate jobs, or decide it is one job.
 *
 * The parallel batch used to be a destination: a screen with eight task boxes, a worker count, a
 * model field and three fusion modes, reached by choosing it before knowing whether the work was
 * parallel. This is what replaced that choice — asking for several things IS the request for a
 * batch, and the only question left is a confirmation, because worktrees are a real side effect.
 *
 * Deliberately deterministic. A model could decompose more cleverly, but the proposal card exists
 * so the user can check the split, and a split they have to check is not worth a second inference:
 * it would cost money, take a turn, and still land in the same card. What is written here is only
 * ever a PROPOSAL — nothing runs until it is confirmed.
 *
 * The bar is intentionally high. A false positive interrupts an ordinary message with a card about
 * git worktrees, which is a worse failure than missing a split the user can make explicit by
 * numbering their lines.
 */

/** A line that opens a list item: `1.` / `1)` / `-` / `*` / `•`, with the marker stripped. */
function listItem(line: string): string | null {
  const m = /^\s*(?:\d{1,2}[.)]|[-*•])\s+(.*)$/.exec(line);
  return m ? m[1].trim() : null;
}

/** The jobs in `message`, or `[]` when it reads as one thing.
 *
 * Only an explicit list counts — numbered or bulleted, at least two items, each substantial enough
 * to be a task. Prose with a comma in it is one job; so is a single bullet under a paragraph. */
export function decompose(message: string): string[] {
  const lines = message.split("\n");
  const items: string[] = [];
  for (const line of lines) {
    const item = listItem(line);
    if (item === null) continue;
    // A bare marker ("- " alone) is a half-typed line, not a job.
    if (item.length >= 8) items.push(item);
  }
  // Two items is the smallest thing that can be parallel; eight is the batch's own worker ceiling,
  // and proposing more than can run would be proposing something we cannot do.
  return items.length >= 2 && items.length <= 8 ? items : [];
}
