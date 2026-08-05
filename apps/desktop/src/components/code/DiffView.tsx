/** A hand-rolled unified-diff renderer: + green, - red, @@ dim headers, file headers muted.
 *
 *  This existed twice — byte-identical in `Code.tsx` and `Agents.tsx`, each with a comment noting
 *  that it mirrored the other. Two copies of a renderer is two places to fix a colour and one place
 *  to forget, so it now lives here and both screens import it.
 *
 *  Deliberately not a diff *parser*: it colours lines by their first character and nothing more.
 *  The patches it renders are real unified diffs produced server-side from the file on disk before
 *  and after a write, so there is nothing here that could invent a change that did not happen. */
export function DiffView({ patch }: { patch: string }) {
  const lines = patch.split("\n");
  return (
    <pre className="overflow-x-auto rounded-chip bg-surface-2 p-2 font-mono text-xs leading-relaxed">
      {lines.map((line, i) => {
        let cls = "text-muted-foreground";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "text-foreground/50";
        else if (line.startsWith("@@")) cls = "text-accent/70";
        else if (line.startsWith("+")) cls = "text-ok";
        else if (line.startsWith("-")) cls = "text-bad";
        return (
          <div key={i} className={cls}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}
