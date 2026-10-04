import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, FileUp, FolderUp, Trash2 } from "lucide-react";

import {
  getSkillBundleText,
  getSkillBundles,
  importSkill,
  setSkillBundleStatus,
  uninstallSkillBundle,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Panel, Spinner } from "@/components/ui/panel";
import { Dialog } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/async";
import { useT } from "@/lib/i18n";
import type { SkillBundle } from "@/lib/types";

/**
 * Skills the owner adds by hand — their own, or one found outside the catalogue.
 *
 * The posture is the catalogue's, said in the same words: adding is not consenting. Whatever the
 * file declares about itself, it lands switched off and marked untrusted, and the owner reads its
 * SKILL.md here before the switch beside it means anything. Before this panel an uploaded bundle
 * would also have been invisible: the catalogue lists only its own entries, so a skill that is in
 * no catalogue had no row and therefore no switch.
 */

/** Junk a picked folder carries that is never part of a skill. Dropped before sending, because a
 *  `.git` directory alone can be thousands of files and the server stops reading at a thousand.
 *  Compiled Python is junk too: any skill whose script ran once has a `__pycache__`, and the server
 *  skips it — sending it only spends the upload's size limit on bytes nobody will keep. */
const LITTER_DIRS = new Set([".git", "__MACOSX", "node_modules", "__pycache__"]);

function worthSending(file: File): boolean {
  const parts = (file.webkitRelativePath || file.name).split("/");
  const last = parts[parts.length - 1] ?? "";
  return !parts.some((part) => LITTER_DIRS.has(part)) && !/\.py[co]$/i.test(last);
}

function Row({ bundle, read, onRead }: { bundle: SkillBundle; read: boolean; onRead: (name: string) => void }) {
  const t = useT();
  const client = useQueryClient();
  const refresh = () => void client.invalidateQueries({ queryKey: ["skill-bundles"] });
  const toggle = useMutation({
    mutationFn: (on: boolean) => setSkillBundleStatus(bundle.name ?? "", on ? "active" : "inactive"),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => uninstallSkillBundle(bundle.name ?? ""), onSuccess: refresh });
  const name = bundle.name ?? "";
  const state = bundle.status ?? "";
  const on = state === "active";
  // "It stays off until you read it" is a promise about the switch, so the switch keeps it: a
  // pending upload cannot be turned on until its SKILL.md has been opened here. Turning off, and
  // turning back on one the owner already read and switched off once, stay one click.
  const unread = state === "pending" && !read;
  const failed = toggle.error ?? remove.error;

  return (
    <div className="flex flex-col gap-2 border-b border-hairline py-3 last:border-0 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-foreground">{name}</span>
          {state && !on ? (
            <Badge tone={state === "pending" ? "warn" : "muted"}>{t(`catalog.state.${state}`)}</Badge>
          ) : null}
          {/* Every time, not only while pending: switching it on is consent to its instructions,
              not a change in where they came from. */}
          <Badge tone="warn">{t("skills.upload.untrusted")}</Badge>
        </div>
        <p className="text-xs text-muted-foreground">{bundle.description}</p>
        {failed ? (
          <p className="text-xs text-bad-foreground">{failed instanceof Error ? failed.message : String(failed)}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => onRead(name)}
          aria-label={t("skills.upload.readOf", { name })}
        >
          <BookOpen className="h-4 w-4" />
          {t("skills.upload.read")}
        </Button>
        <Button
          size="sm"
          variant={on ? "primary" : "outline"}
          disabled={toggle.isPending || (!on && unread)}
          title={!on && unread ? t("skills.upload.readFirst") : undefined}
          onClick={() => toggle.mutate(!on)}
        >
          {on ? t("catalog.on") : t("catalog.off")}
        </Button>
        <button
          type="button"
          aria-label={t("catalog.uninstallOf", { name })}
          title={t("catalog.uninstall")}
          disabled={remove.isPending}
          onClick={() => remove.mutate()}
          className="text-muted-foreground hover:text-bad"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

export function SkillUpload() {
  const t = useT();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [reading, setReading] = useState<string | null>(null);
  // Names whose SKILL.md was on the screen in this session. Not persisted: the switch is the
  // consent, and consent to a text is given by reading it now, not by having read some version once.
  const [read, setRead] = useState<ReadonlySet<string>>(() => new Set());
  // Kept so a 409 can be answered with "replace it" without asking the owner to pick the files again.
  const [lastPick, setLastPick] = useState<File[]>([]);
  const bundles = useQuery({ queryKey: ["skill-bundles"], queryFn: getSkillBundles });
  const text = useQuery({
    queryKey: ["skill-bundle-text", reading],
    queryFn: () => getSkillBundleText(reading as string),
    enabled: reading !== null,
  });
  const upload = useMutation({
    mutationFn: ({ files, replace }: { files: File[]; replace: boolean }) => importSkill(files, replace),
    onSuccess: (added) => {
      // A replacement is new text: what was read before is not what would be switched on now.
      const name = added.name ?? "";
      setRead((prev) => {
        if (!prev.has(name)) return prev;
        const next = new Set(prev);
        next.delete(name);
        return next;
      });
      void client.invalidateQueries({ queryKey: ["skill-bundle-text", name] });
      void client.invalidateQueries({ queryKey: ["skill-bundles"] });
      void client.invalidateQueries({ queryKey: ["skill-catalog"] });
    },
  });

  // Counted as read once the text is on the screen, not when the button is pressed: a dialog that
  // failed to load showed nothing to consent to.
  const shown = text.data?.name;
  useEffect(() => {
    if (!shown || reading !== shown) return;
    setRead((prev) => (prev.has(shown) ? prev : new Set(prev).add(shown)));
  }, [shown, reading]);

  // `webkitdirectory` is not in React's attribute types, and it is the only way a browser offers a
  // folder picker. Set on the element rather than spread as an untyped prop.
  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);

  const send = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter(worthSending);
    if (files.length === 0) return;
    setLastPick(files);
    upload.mutate({ files, replace: false });
  };

  // Read off the error rather than tested with `instanceof ApiError`: the status is the whole
  // question, and a check that depends on which class the error was built from fails closed the
  // day something wraps it.
  const taken = (upload.error as { status?: unknown } | null)?.status === 409;
  const mine = (bundles.data ?? []).filter((b) => b.origin === "upload");

  return (
    <Panel title={t("skills.upload.title")}>
      <div className="px-4 py-3">
        {/* The catalogue's own sentence about switching, before the buttons rather than after the
            upload: this is the decision the buttons below do NOT make. */}
        <p className="mb-3 text-xs text-muted-foreground">{t("skills.upload.consent")}</p>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>
            {upload.isPending ? <Spinner /> : <FileUp className="h-4 w-4" />}
            {t("skills.upload.file")}
          </Button>
          <Button size="sm" variant="outline" disabled={upload.isPending} onClick={() => folderInput.current?.click()}>
            <FolderUp className="h-4 w-4" />
            {t("skills.upload.folder")}
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept=".zip,.md"
            className="hidden"
            data-testid="skill-upload-file"
            onChange={(event) => {
              send(event.target.files);
              event.target.value = "";
            }}
          />
          <input
            ref={folderInput}
            type="file"
            multiple
            className="hidden"
            data-testid="skill-upload-folder"
            onChange={(event) => {
              send(event.target.files);
              event.target.value = "";
            }}
          />
        </div>

        {upload.isSuccess && upload.data ? (
          <p className="mb-3 text-xs text-muted-foreground" role="status">
            {t("skills.upload.added", { name: upload.data.name ?? "" })}
          </p>
        ) : null}
        {upload.error ? (
          <div className="mb-3 flex flex-wrap items-center gap-2" role="alert">
            <p className="text-xs text-bad-foreground">
              {upload.error instanceof Error ? upload.error.message : String(upload.error)}
            </p>
            {taken ? (
              <Button size="sm" variant="outline" onClick={() => upload.mutate({ files: lastPick, replace: true })}>
                {t("skills.upload.replace")}
              </Button>
            ) : null}
          </div>
        ) : null}

        {bundles.isError ? (
          <ErrorState error={bundles.error} onRetry={() => void bundles.refetch()} />
        ) : bundles.isLoading ? (
          <Spinner />
        ) : mine.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("skills.upload.empty")}</p>
        ) : (
          mine.map((bundle) => (
            <Row key={bundle.name} bundle={bundle} read={read.has(bundle.name ?? "")} onRead={setReading} />
          ))
        )}
      </div>

      <Dialog open={reading !== null} onOpenChange={(next) => !next && setReading(null)} title={reading ?? ""}>
        {text.isError ? (
          <ErrorState error={text.error} onRetry={() => void text.refetch()} />
        ) : text.isLoading || !text.data ? (
          <Spinner />
        ) : (
          <>
            {/* As text, not rendered — the same choice the library makes: what the owner reads
                should be what the agent would read, not a prettier document than the one it gets. */}
            <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap wrap-break-word text-xs text-muted-foreground">
              {text.data.text}
            </pre>
            {text.data.truncated ? (
              <p className="mt-2 text-xs text-warn-foreground">{t("skills.upload.truncated")}</p>
            ) : null}
          </>
        )}
      </Dialog>
    </Panel>
  );
}
