import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { getAppDiagnostics, getTroubleshoot, openAppFolder, resetAppData } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * What a bug report needs, in one place (study 29, P5.3): the backend's version, the installation
 * id, the folders the desktop fixed for it, the last crash report it wrote, and a Copy button for
 * all of it.
 *
 * Every line comes from `GET /api/diagnostics`, which scrubs credentials before anything leaves the
 * backend — the crash report is the backend's stderr, and a provider error there quotes the request
 * it failed on. The card says so, because "may I paste this in a public issue?" is the question a
 * person has before pressing Copy.
 *
 * Troubleshooting (C7): open the logs or data folder, and reset the app's data. The window grants
 * the page no native calls, so these go through the backend, which leaves the desktop app an action
 * NAME (`chimera/api/shell_requests.py`); the desktop app owns every path. A backend started without
 * the desktop app says `available: false`, and the buttons say why they are off.
 *
 * The reset asks for a typed word — the server's, compared exactly — and MOVES the data folder to a
 * dated backup rather than deleting it; after the restart, the card shows where the backup went.
 */
export function DiagnosticsCard() {
  const t = useT();
  const headingId = useId();
  const wordId = useId();
  const qc = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [idCopied, setIdCopied] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [typed, setTyped] = useState("");
  const diag = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => getAppDiagnostics(),
    retry: false,
  });
  const trouble = useQuery({
    queryKey: ["troubleshoot"],
    queryFn: () => getTroubleshoot(),
    retry: false,
  });
  // Wrapped, not passed by reference, for the reason `FoldersCard` gives: a client function that is
  // missing must fail this card's action, not the Settings screen.
  const open = useMutation({ mutationFn: (folder: "logs" | "data") => openAppFolder(folder) });
  const reset = useMutation({
    mutationFn: (word: string) => resetAppData(word),
    onSuccess: () => {
      setResetting(false);
      setTyped("");
      void qc.invalidateQueries({ queryKey: ["troubleshoot"] });
    },
  });
  const d = diag.data;
  const shell = trouble.data;
  const canAsk = shell?.available === true;
  const word = shell?.reset_word ?? "";

  const copy = (text: string, done: () => void) => {
    // Best-effort: the clipboard is absent in a non-secure context and rejects without focus. A
    // failed copy leaves the button as it was rather than claiming success.
    void navigator.clipboard
      ?.writeText(text)
      .then(done)
      .catch(() => undefined);
  };

  const rows: [string, string][] = d
    ? [
        [t("settings.diag.backendVersion"), d.backend_version],
        [t("settings.diag.installId"), d.install_id],
        [t("settings.diag.platform"), `${d.platform} · Python ${d.python}`],
        [t("settings.diag.home"), d.home],
        [t("settings.diag.workspace"), d.workspace],
        [t("settings.diag.worktrees"), d.worktree_dir],
      ]
    : [];

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.diagnostics")}
      </h2>
      <div className="divide-y divide-hairline">
        {diag.isError && (
          <p role="status" className="px-4 py-2.5 text-xs text-bad-foreground">
            {t("settings.diag.unavailable")}
          </p>
        )}
        {d && (
          <dl className="space-y-1.5 px-4 py-3">
            {rows.map(([label, value]) => (
              <div key={label} className="flex items-baseline justify-between gap-3">
                <dt className="shrink-0 text-sm">{label}</dt>
                <dd className="min-w-0 break-all text-right font-mono text-xs">{value}</dd>
              </div>
            ))}
          </dl>
        )}
        {d && (
          <div className="px-4 py-3">
            {d.crash ? (
              <details className="text-xs">
                <summary className="cursor-pointer text-warn-foreground">
                  {t("settings.diag.crash", { when: d.crash.modified })}
                </summary>
                <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-chip bg-surface-2 p-2 font-mono text-xs text-muted-foreground">
                  {d.crash.text}
                </pre>
              </details>
            ) : (
              <p className="text-xs text-muted-foreground">{t("settings.diag.noCrash")}</p>
            )}
          </div>
        )}
        {d && (
          <div className="flex flex-wrap items-center gap-3 px-4 py-3">
            <Button size="sm" variant="outline" onClick={() => copy(d.report, () => setCopied(true))}>
              {copied ? t("settings.diag.copied") : t("settings.diag.copy")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => copy(d.install_id, () => setIdCopied(true))}
            >
              {idCopied ? t("settings.diag.installIdCopied") : t("settings.diag.copyInstallId")}
            </Button>
            <span className="text-xs text-muted-foreground">{t("settings.diag.scrubbed")}</span>
          </div>
        )}
        {shell && (
          <div className="space-y-2 px-4 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={!canAsk || open.isPending}
                onClick={() => open.mutate("logs")}
              >
                {t("settings.trouble.openLogs")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!canAsk || open.isPending}
                onClick={() => open.mutate("data")}
              >
                {t("settings.trouble.openData")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!canAsk || reset.isPending}
                onClick={() => setResetting(true)}
              >
                {t("settings.trouble.reset")}
              </Button>
            </div>
            {!canAsk && (
              <p className="text-xs text-muted-foreground">{t("settings.trouble.desktopOnly")}</p>
            )}
            {resetting && (
              <div className="flex flex-col gap-2">
                <p className="text-xs text-warn-foreground">{t("settings.trouble.resetExplain")}</p>
                <label htmlFor={wordId} className="text-xs">
                  {t("settings.trouble.resetType", { word })}
                </label>
                <input
                  id={wordId}
                  className="field h-8 min-w-0 px-2.5 font-mono text-xs"
                  value={typed}
                  autoComplete="off"
                  spellCheck={false}
                  onChange={(e) => setTyped(e.target.value)}
                />
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={word === "" || typed !== word || reset.isPending}
                    onClick={() => reset.mutate(typed)}
                  >
                    {t("settings.trouble.resetConfirm")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      setResetting(false);
                      setTyped("");
                    }}
                  >
                    {t("common.cancel")}
                  </Button>
                </div>
              </div>
            )}
            {reset.isSuccess && (
              <p role="status" className="text-xs text-muted-foreground">
                {t("settings.trouble.resetRequested")}
              </p>
            )}
            {(open.isError || reset.isError) && (
              <p role="status" className="text-xs text-bad-foreground">
                {t("settings.trouble.failed")}
              </p>
            )}
            {shell.last_reset && (
              <p className="break-all text-xs text-muted-foreground">
                {shell.last_reset.moved
                  ? t("settings.trouble.lastResetMoved", { path: shell.last_reset.detail })
                  : t("settings.trouble.lastResetFailed", { why: shell.last_reset.detail })}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
