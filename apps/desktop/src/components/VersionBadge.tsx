import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpCircle, Check, Copy, ExternalLink } from "lucide-react";
import { getVersion } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// We link to the release and show the pip command — we deliberately do NOT auto-install in place. The
// Tauri in-place updater is future work (see CHANGELOG); until then updating is a one-line pip command.
const PIP_CMD = "pip install -U 'chimera-agent[desktop]'";
// Persist which version the user chose to skip, so we don't nag every launch for a version they passed on.
const DISMISS_KEY = "chimera.updateDismissed";

/** A low-key version indicator in the app chrome (bottom corner). When GitHub confirms a strictly-newer
 *  release it turns into a clickable accent pill — "v{latest} available" — opening a small dismissible
 *  prompt with the release link and the pip command (no in-place install; that's the Tauri updater,
 *  future work). Honest by construction: the backend only reports `update_available` when a newer
 *  release is CONFIRMED, so offline / any error just shows the quiet current version. */
export function VersionBadge() {
  const t = useT();
  const { data } = useQuery({
    queryKey: ["version"],
    queryFn: getVersion,
    // Fetch once on load; the backend already caches the GitHub result for an hour, so don't spam.
    staleTime: 1000 * 60 * 60,
    refetchOnWindowFocus: false,
    retry: false,
  });
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(() => localStorage.getItem(DISMISS_KEY));

  if (!data) return null;

  const latest = data.latest ?? "";
  // Only signal when an update is confirmed AND the user hasn't chosen to skip THIS version.
  const canUpdate = data.update_available && !!latest && dismissed !== latest;

  if (!canUpdate) {
    // Quiet state: just the current version, non-interactive.
    return (
      <span className="select-none px-2 py-1 text-[11px] tabular-nums text-muted-foreground/60">
        v{data.version}
      </span>
    );
  }

  const dismiss = () => {
    localStorage.setItem(DISMISS_KEY, latest);
    setDismissed(latest);
    setOpen(false);
  };

  const copy = () => {
    void navigator.clipboard?.writeText(PIP_CMD).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div className="relative">
      {open && (
        <div className="surface absolute bottom-full right-0 mb-2 w-72 space-y-3 p-3 text-sm shadow-elev">
          <p className="text-foreground">{t("update.prompt", { latest })}</p>
          <p className="text-xs text-muted-foreground">{t("update.howto")}</p>
          <div className="flex items-center justify-between gap-2 rounded-chip bg-white/[0.05] px-2 py-1.5 ring-1 ring-white/5">
            <code className="truncate font-mono text-[11px] text-muted-foreground">{PIP_CMD}</code>
            <button
              onClick={copy}
              title={copied ? t("update.copied") : t("update.copy")}
              className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-ok" /> : <Copy className="h-3.5 w-3.5" />}
            </button>
          </div>
          <div className="flex items-center justify-between">
            {data.notes_url ? (
              <a
                href={data.notes_url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-accent hover:underline"
              >
                {t("update.viewRelease")}
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            ) : (
              <span />
            )}
            <button
              onClick={dismiss}
              className="text-muted-foreground transition-colors hover:text-foreground"
            >
              {t("update.dismiss")}
            </button>
          </div>
        </div>
      )}
      <button
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "inline-flex items-center gap-1.5 rounded-chip px-2 py-1 text-[11px] font-medium",
          "bg-accent/15 text-accent ring-1 ring-accent/25 transition-colors hover:bg-accent/25",
        )}
      >
        <ArrowUpCircle className="h-3.5 w-3.5" />
        {t("update.available", { latest })}
      </button>
    </div>
  );
}
