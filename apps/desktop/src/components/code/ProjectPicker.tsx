import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronUp, Folder, FolderPlus, Loader2 } from "lucide-react";

import { browseDirs, makeDir } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";

/** Pick a project folder by clicking, not by typing a path.
 *
 * Typing `/mnt/c/Users/you/projects/thing` is a developer's interface, and this app is meant to be
 * usable by whoever installs it. The obvious answer — the operating system's own folder dialog —
 * turned out to be unavailable: the window loads the local backend's origin
 * (`WebviewUrl::External`, `withGlobalTauri` off), so Tauri IPC is not reachable from the page, and
 * the dialog plugin works over IPC. Opening IPC to that origin would widen what an XSS in our own
 * UI could reach, to make a dialog look native rather than do anything a listing cannot.
 *
 * So this browses through the backend instead. It behaves identically in the packaged window and in
 * a browser tab, which the native dialog never would, and it needs no change to the security
 * posture someone chose on purpose. The endpoint behind it lists DIRECTORIES only and reads
 * nothing.
 */
export function ProjectPicker({
  onPick,
  onCancel,
}: {
  onPick: (path: string) => void;
  onCancel: () => void;
}) {
  const t = useT();
  const [at, setAt] = useState("");
  // The name being typed, or null when the field is closed. Not a boolean plus a string: "closed"
  // and "open and empty" are different states and only one of them should render a field.
  const [naming, setNaming] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["fs-browse", at], queryFn: () => browseDirs(at) });
  const create = useMutation({
    mutationFn: (name: string) => makeDir(q.data?.path ?? at, name),
    // Straight into the folder that was just made. Creating one and staying in the parent means
    // the next click is "Use this folder" on the WRONG folder — the one the new one is inside.
    onSuccess: (made) => {
      setNaming(null);
      setFailed(false);
      void qc.invalidateQueries({ queryKey: ["fs-browse"] });
      setAt(made.path);
    },
    // The server refuses a name that cannot be a folder, and the reason belongs on screen: a
    // silent no-op reads as a broken button, and the fix is one character away.
    onError: () => setFailed(true),
  });

  return (
    <div className="flex max-h-80 w-full max-w-lg flex-col rounded-chip border border-border bg-surface-2 shadow-lg">
      <div className="flex items-center gap-2 border-b border-hairline px-3 py-2">
        <Button
          size="sm"
          variant="ghost"
          disabled={!q.data?.parent}
          onClick={() => setAt(q.data?.parent ?? "")}
          aria-label={t("code.picker.up")}
        >
          <ChevronUp className="h-4 w-4" />
        </Button>
        {/* The current path, always visible. A picker that shows only folder names leaves you one
            wrong click from choosing the right NAME in the wrong place. */}
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
          {q.data?.path ?? "…"}
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {q.isLoading ? (
          <div className="flex justify-center py-6 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : (q.data?.entries.length ?? 0) === 0 ? (
          <p className="px-3 py-4 text-xs text-muted-foreground">{t("code.picker.emptyDir")}</p>
        ) : (
          q.data?.entries.map((dir) => (
            <button
              key={dir.path}
              type="button"
              onDoubleClick={() => setAt(dir.path)}
              onClick={() => setAt(dir.path)}
              className="flex w-full items-center gap-1.5 px-3 py-1 text-left text-xs text-muted-foreground hover:text-foreground"
            >
              <Folder className="h-3.5 w-3.5 shrink-0 text-accent/80" />
              <span className="truncate">{dir.name}</span>
            </button>
          ))
        )}
        {q.data?.capped ? (
          // Silence here would read as "that is all of them", which is the one thing a truncated
          // listing must not imply.
          <p className="px-3 py-2 text-xs text-muted-foreground">{t("code.picker.capped")}</p>
        ) : null}
      </div>

      {naming !== null ? (
        <form
          className="flex items-center gap-2 border-t border-hairline px-3 py-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (naming.trim()) create.mutate(naming.trim());
          }}
        >
          <input
            autoFocus
            aria-label={t("code.picker.newFolderName")}
            placeholder={t("code.picker.newFolderName")}
            className="field h-8 min-w-0 flex-1 px-2 text-sm"
            value={naming}
            onChange={(e) => {
              setNaming(e.target.value);
              setFailed(false);
            }}
            onKeyDown={(e) => e.key === "Escape" && setNaming(null)}
          />
          <Button size="sm" type="submit" disabled={!naming.trim() || create.isPending}>
            {t("code.picker.create")}
          </Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => setNaming(null)}>
            {t("code.picker.cancel")}
          </Button>
        </form>
      ) : null}
      {failed ? (
        <p className="px-3 pb-2 text-xs text-bad-foreground">{t("code.picker.newFolderFailed")}</p>
      ) : null}

      <div className="flex items-center gap-2 border-t border-hairline px-3 py-2">
        <Button size="sm" onClick={() => onPick(q.data?.path ?? "")} disabled={!q.data}>
          {t("code.picker.useThis")}
        </Button>
        {/* The gap this picker had: it could only SELECT, so a new project began in Explorer — and
            the folder people then picked was often the wrong one, because the right one did not
            exist yet. */}
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setNaming("");
            setFailed(false);
          }}
          disabled={!q.data}
        >
          <FolderPlus className="mr-1 h-3.5 w-3.5" />
          {t("code.picker.newFolder")}
        </Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>
          {t("code.picker.cancel")}
        </Button>
      </div>
    </div>
  );
}
