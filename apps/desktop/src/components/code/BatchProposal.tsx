import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Boxes } from "lucide-react";

import { getGitStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { GitInitButton } from "@/components/code/GitInitButton";
import { useT } from "@/lib/i18n";

/** The one question the parallel batch still asks.
 *
 * Running several agents at once used to be a destination you chose before knowing whether the work
 * was parallel — a screen with eight task boxes, a worker count, a model field and three fusion
 * modes. Asking for several things IS the request now, and this is the confirmation, because git
 * worktrees are a real side effect and creating them silently would be creating them behind
 * someone's back.
 *
 * The isolation warning is the reason this card exists at all rather than a toast afterwards. The
 * old screen showed "this batch ran WITHOUT isolation" in the results banner — after N agents had
 * already edited the same directory, which is the point at which nothing can be done about it. Here
 * it is a fact about what WOULD happen, next to the button that would make it happen.
 *
 * And now next to the fix, too. Naming a problem the user cannot act on from where they are reading
 * about it leaves them the choice between running the batch unprotected and going to find a
 * terminal — so the warning carries the one-press repair, taken BEFORE the agents start writing,
 * which is the only time it can be taken.
 */
export function BatchProposal({
  tasks,
  workspace,
  onConfirm,
  onDecline,
}: {
  tasks: string[];
  workspace: string;
  onConfirm: () => void;
  onDecline: () => void;
}) {
  const t = useT();
  const git = useQuery({
    queryKey: ["git-status", workspace],
    queryFn: () => getGitStatus(workspace || null),
  });
  // Only when we KNOW it is not a repo. While the answer is loading, saying nothing is right and
  // saying "no isolation" would be a warning invented out of a pending request.
  const noIsolation = git.data ? !git.data.is_repo : false;

  return (
    <div className="space-y-2 rounded-chip border border-accent/40 bg-surface-2 p-3">
      <p className="flex items-start gap-1.5 text-sm text-foreground/90">
        <Boxes className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
        {t("code.batch.proposal", { n: tasks.length })}
      </p>
      <ol className="list-decimal space-y-0.5 pl-8 text-sm text-muted-foreground">
        {tasks.map((task, i) => (
          <li key={i}>{task}</li>
        ))}
      </ol>
      {noIsolation ? (
        <div className="space-y-2">
          <p className="flex items-start gap-1.5 text-xs text-bad-foreground">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {t("code.batch.noIsolation")}
          </p>
          <GitInitButton workspace={workspace} />
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onConfirm}>
          <Boxes className="h-3.5 w-3.5" /> {t("code.batch.confirm", { n: tasks.length })}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDecline}>
          {t("code.batch.decline")}
        </Button>
      </div>
    </div>
  );
}
