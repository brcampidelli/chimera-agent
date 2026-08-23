import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FolderGit2, Loader2 } from "lucide-react";

import { gitInit } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/** "Initialise git here", offered wherever the app currently notices the folder is not a repo.
 *
 * Two places diagnosed this correctly and then gave up: the git panel's empty state and the batch
 * proposal's isolation warning both told the user to run `git init` in a terminal — in an app whose
 * whole claim is that you do not need one. The sentence was translated into ten languages, which is
 * ten translations of an instruction to leave.
 *
 * One component rather than two copies, because the two call sites want the same three things (a
 * button, an honest failure, a refreshed git status) and the difference between two near-copies is
 * the bug that appears in one of them a year later.
 *
 * What the press does is `git init` PLUS a snapshot commit, server-side. That ordering is the point:
 * this button is pressed at the moment before an agent is given write and shell access to the
 * folder, and a repo with no commit in it is isolation with nothing to return to.
 *
 * A failure is shown, not swallowed. The most likely one is "already a git repo" from a second
 * press — harmless, and still worth saying, because the alternative is a button that looks like it
 * did nothing.
 */
export function GitInitButton({ workspace }: { workspace: string }) {
  const t = useT();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  async function init() {
    if (busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const res = await gitInit(workspace || null);
      if (res.ok) {
        // Everything that was empty because there was no repo can now describe one: this panel, the
        // batch proposal's isolation warning, and the tree (a fresh `.git` is pruned from it, but a
        // stale listing taken before the init would show it).
        await qc.invalidateQueries({ queryKey: ["git-status", workspace] });
        void qc.invalidateQueries({ queryKey: ["fs-tree"] });
      } else {
        setFailed(true);
      }
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="outline" disabled={busy} onClick={() => void init()}>
        {busy ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
        ) : (
          <FolderGit2 className="h-3.5 w-3.5" />
        )}
        {t("code.git.init")}
      </Button>
      {failed ? <span className="text-xs text-bad-foreground">{t("code.git.initError")}</span> : null}
    </div>
  );
}
