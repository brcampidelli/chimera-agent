import { useEffect, useRef, useState } from "react";
import * as Menu from "@radix-ui/react-dropdown-menu";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileUp, FolderUp, Loader2, Plus } from "lucide-react";

import { addPluginMarketplace, installPlugin, uploadPlugin, validatePluginFolder } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";

type Which = "" | "marketplace" | "upload" | "create";

const ITEM =
  "flex cursor-default items-center gap-2 rounded-md px-2 py-1 text-sm outline-hidden data-highlighted:bg-surface-hover";
const SHA = /^[0-9a-f]{40}$/;
const REPO = /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/;
/** Never part of a plugin, and a `.git` folder alone can be thousands of files. */
const LITTER = new Set([".git", "__MACOSX", "node_modules", "__pycache__"]);
const DOCS = "https://code.claude.com/docs/en/plugins";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** GitHub pinned to a commit, or a local folder. No branch: a branch names whatever is there
 *  tomorrow, and the marketplace's entries are what the owner is shown today. */
function MarketplaceForm({ onDone }: { onDone: () => void }) {
  const t = useT();
  const client = useQueryClient();
  const [repo, setRepo] = useState("");
  const [sha, setSha] = useState("");
  const [folder, setFolder] = useState("");
  const add = useMutation({
    mutationFn: () => addPluginMarketplace(folder.trim() || `github:${repo.trim()}@${sha.trim()}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["plugin-markets"] });
      onDone();
    },
  });
  const ready = folder.trim() !== "" || (REPO.test(repo.trim()) && SHA.test(sha.trim()));
  return (
    <form
      className="space-y-3 text-sm"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready) add.mutate();
      }}
    >
      <p className="text-xs text-muted-foreground">{t("plugins.market.why")}</p>
      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">{t("plugins.market.repo")}</span>
        <input className="field h-8 w-full px-2" value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="owner/repo" />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">{t("plugins.market.sha")}</span>
        <input className="field h-8 w-full px-2 font-mono" value={sha} onChange={(e) => setSha(e.target.value)} />
      </label>
      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">{t("plugins.market.local")}</span>
        <input className="field h-8 w-full px-2 font-mono" value={folder} onChange={(e) => setFolder(e.target.value)} />
      </label>
      {add.error ? <p role="alert" className="text-xs text-bad-foreground">{message(add.error)}</p> : null}
      <div className="flex justify-end">
        <Button size="sm" type="submit" disabled={!ready || add.isPending}>
          {add.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {t("plugins.market.submit")}
        </Button>
      </div>
    </form>
  );
}

/** An archive or a picked folder. Lands pending; a name already installed asks before replacing,
 *  and a replacement is an update — held or pending with its diff, never on. */
function UploadForm({ onDone }: { onDone: () => void }) {
  const t = useT();
  const client = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [last, setLast] = useState<File[]>([]);
  const upload = useMutation({
    mutationFn: ({ files, replace }: { files: File[]; replace: boolean }) => uploadPlugin(files, replace),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["plugins"] });
      onDone();
    },
  });
  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, []);
  const send = (list: FileList | null) => {
    const files = Array.from(list ?? []).filter(
      (f) => !(f.webkitRelativePath || f.name).split("/").some((part) => LITTER.has(part)),
    );
    if (files.length === 0) return;
    setLast(files);
    upload.mutate({ files, replace: false });
  };
  const taken = (upload.error as { status?: unknown } | null)?.status === 409;
  return (
    <div className="space-y-3 text-sm">
      <p className="text-xs text-muted-foreground">{t("plugins.upload.consent")}</p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>
          {upload.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
          {t("plugins.upload.file")}
        </Button>
        <Button size="sm" variant="outline" disabled={upload.isPending} onClick={() => folderInput.current?.click()}>
          <FolderUp className="h-4 w-4" />
          {t("plugins.upload.folder")}
        </Button>
        <input
          ref={fileInput}
          type="file"
          accept=".zip,.tar.gz,.tgz,.tar"
          className="hidden"
          data-testid="plugin-upload-file"
          onChange={(e) => {
            send(e.target.files);
            e.target.value = "";
          }}
        />
        <input
          ref={folderInput}
          type="file"
          multiple
          className="hidden"
          data-testid="plugin-upload-folder"
          onChange={(e) => {
            send(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      {upload.error ? (
        <div role="alert" className="flex flex-wrap items-center gap-2">
          <p className="text-xs text-bad-foreground">{message(upload.error)}</p>
          {taken ? (
            <Button size="sm" variant="outline" onClick={() => upload.mutate({ files: last, replace: true })}>
              {t("plugins.upload.replace")}
            </Button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** No builder yet, and said so: the format is Claude Code's, written by hand and checked here. A
 *  checked folder can be installed from the same place, pending like any other. */
function CreateHelp({ onDone }: { onDone: () => void }) {
  const t = useT();
  const client = useQueryClient();
  const [path, setPath] = useState("");
  const check = useMutation({ mutationFn: () => validatePluginFolder(path.trim()) });
  const install = useMutation({
    mutationFn: () => installPlugin({ source: path.trim() }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["plugins"] });
      onDone();
    },
  });
  const report = check.data;
  const problems = report?.problems ?? [];
  return (
    <div className="space-y-3 text-sm">
      <p className="text-xs text-muted-foreground">{t("plugins.create.body")}</p>
      <a className="text-xs text-accent-ink underline" href={DOCS} target="_blank" rel="noreferrer">
        {t("plugins.create.docs")}
      </a>
      <label className="block space-y-1">
        <span className="text-xs text-muted-foreground">{t("plugins.validate.path")}</span>
        <input className="field h-8 w-full px-2 font-mono" value={path} onChange={(e) => setPath(e.target.value)} />
      </label>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" disabled={!path.trim() || check.isPending} onClick={() => check.mutate()}>
          {t("plugins.validate.run")}
        </Button>
        {report?.ok && report.kind === "plugin" ? (
          <Button size="sm" disabled={install.isPending} onClick={() => install.mutate()}>
            {t("plugins.local.install")}
          </Button>
        ) : null}
      </div>
      {report ? (
        <div role="status" className="space-y-1 text-xs">
          <p>{report.ok ? t("plugins.validate.ok", { name: report.name ?? "" }) : t("plugins.validate.bad", { n: problems.length })}</p>
          <ul className="space-y-0.5 text-muted-foreground">
            {problems.map((p, i) => (
              <li key={`${p.code}-${i}`} className={p.severity === "error" ? "text-bad-foreground" : ""}>
                <span className="font-mono">{p.where}</span> {p.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {check.error ?? install.error ? (
        <p role="alert" className="text-xs text-bad-foreground">{message(check.error ?? install.error)}</p>
      ) : null}
    </div>
  );
}

export function AddPluginMenu() {
  const t = useT();
  const [which, setWhich] = useState<Which>("");
  const close = () => setWhich("");
  const titles: Record<Exclude<Which, "">, string> = {
    marketplace: t("plugins.add.marketplace"),
    upload: t("plugins.add.upload"),
    create: t("plugins.add.create"),
  };
  return (
    <>
      <Menu.Root>
        <Menu.Trigger asChild>
          <Button size="sm" variant="outline">
            <Plus className="h-4 w-4" />
            {t("plugins.add")}
          </Button>
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content align="end" sideOffset={4} className="overlay floating z-50 min-w-56 p-1 text-foreground">
            <Menu.Item className={ITEM} onSelect={() => setWhich("marketplace")}>
              {t("plugins.add.marketplace")}
            </Menu.Item>
            <Menu.Item className={ITEM} onSelect={() => setWhich("upload")}>
              {t("plugins.add.upload")}
            </Menu.Item>
            <Menu.Item className={ITEM} onSelect={() => setWhich("create")}>
              {t("plugins.add.create")}
            </Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
      <Dialog open={which !== ""} onOpenChange={(open) => !open && close()} title={which ? titles[which] : ""}>
        {which === "marketplace" ? <MarketplaceForm onDone={close} /> : null}
        {which === "upload" ? <UploadForm onDone={close} /> : null}
        {which === "create" ? <CreateHelp onDone={close} /> : null}
      </Dialog>
    </>
  );
}
