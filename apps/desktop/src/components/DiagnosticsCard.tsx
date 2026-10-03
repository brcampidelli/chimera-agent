import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { getAppDiagnostics } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * What a bug report needs, in one place (study 29, P5.3): the backend's version, the folders the
 * desktop fixed for it, the last crash report it wrote, and a Copy button for all of it.
 *
 * Read-only. Every line comes from `GET /api/diagnostics`, which scrubs credentials before anything
 * leaves the backend — the crash report is the backend's stderr, and a provider error there quotes
 * the request it failed on. The card says so, because "may I paste this in a public issue?" is the
 * question a person has before pressing Copy.
 *
 * Not here: a button that opens the data folder in the file explorer. The window grants the page no
 * native calls, and opening a local folder would need a new one (the reason `FoldersCard` gives); the
 * path is shown so it can be copied instead. Nor the desktop shell's own version, which the page has
 * no way to read — the crash report's header carries it when there is one.
 */
export function DiagnosticsCard() {
  const t = useT();
  const headingId = useId();
  const [copied, setCopied] = useState(false);
  const diag = useQuery({
    queryKey: ["diagnostics"],
    queryFn: () => getAppDiagnostics(),
    retry: false,
  });
  const d = diag.data;

  const rows: [string, string][] = d
    ? [
        [t("settings.diag.backendVersion"), d.backend_version],
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
          <div className="flex items-center gap-3 px-4 py-3">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                // Best-effort: the clipboard is absent in a non-secure context and rejects without
                // focus. A failed copy leaves the button as it was rather than claiming success.
                void navigator.clipboard
                  ?.writeText(d.report)
                  .then(() => setCopied(true))
                  .catch(() => undefined);
              }}
            >
              {copied ? t("settings.diag.copied") : t("settings.diag.copy")}
            </Button>
            <span className="text-xs text-muted-foreground">{t("settings.diag.scrubbed")}</span>
          </div>
        )}
      </div>
    </section>
  );
}
