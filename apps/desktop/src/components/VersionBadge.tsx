import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpCircle, Check, Copy, ExternalLink } from "lucide-react";
import { getVersion } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { isNativeShell } from "@/lib/shell";
import { cn } from "@/lib/utils";

// The pip command is right for a BROWSER session (`chimera app`), where the thing to update really
// is the Python package. It is wrong inside the installed bundle, which ships a complete signed
// updater — `tauri-plugin-updater` checks at launch, verifies against the embedded pubkey, asks,
// installs and restarts — and where this command would update a package the user is not running.
// The comment that used to sit here called that updater "future work"; it shipped.
const PIP_CMD = "pip install -U 'chimera-agent[desktop]'";
// Persist which version the user chose to skip, so we don't nag every launch for a version they passed on.
const DISMISS_KEY = "chimera.updateDismissed";

/** A low-key version indicator in the app chrome (bottom corner). When GitHub confirms a strictly-newer
 *  release it turns into a clickable accent pill — "v{latest} available" — opening a small dismissible
 *  prompt with the release link and, in a browser session, the pip command.
 *
 *  Inside the installed bundle it says what actually happens there instead: the native updater already
 *  offered this release at launch, and it installs in place. Handing that user a pip command was
 *  pointing them at a different copy of the software from the one on their screen.
 *
 *  Honest by construction: the backend only reports `update_available` when a newer release is
 *  CONFIRMED, so offline / any error just shows the quiet current version. */
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
  const native = isNativeShell();

  if (!data) return null;

  const latest = data.latest ?? "";
  // Only signal when an update is confirmed AND the user hasn't chosen to skip THIS version.
  const canUpdate = data.update_available && !!latest && dismissed !== latest;

  if (!canUpdate) {
    // Quiet state: just the current version, non-interactive.
    return (
      <span className="select-none px-2 py-1 text-xs tabular-nums text-muted-foreground/60">
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
          {/* The native panel STATES; the browser one asks.
              It used to ask in both: "A new version is available. Update?" — over a panel whose
              only two buttons are "View release" and "Dismiss". A question mark promises an answer,
              and inside the bundle there was none to give: this panel lives in the webview, on the
              sidecar's http origin, with no IPC to the Rust updater (see `capabilities/default.json`
              and `idioma_do_dialogo` in `main.rs`). It could not start an update if it wanted to.
              In a browser the question is fair — the pip command below IS the answer. */}
          <p className="text-foreground">
            {native ? t("update.promptNative", { latest }) : t("update.prompt", { latest })}
          </p>
          {/* And it now names the thing that DOES start it. `update.trayItem` is the tray label,
              which also exists in `main.rs` — two tables for one string, so a Rust test compares
              them and fails if they drift. Pointing at a menu item spelled differently from the
              menu item would be worse than pointing at nothing. */}
          <p className="text-xs text-muted-foreground">
            {native
              ? t("update.howtoNative", { item: t("update.trayItem") })
              : t("update.howto")}
          </p>
          {native ? null : (
            <div className="flex items-center justify-between gap-2 rounded-chip bg-surface-2 px-2 py-1.5 ring-1 ring-hairline">
              <code className="truncate font-mono text-xs text-muted-foreground">{PIP_CMD}</code>
              <button
                onClick={copy}
                title={copied ? t("update.copied") : t("update.copy")}
                className="shrink-0 text-muted-foreground transition-colors hover:text-foreground"
              >
                {copied ? <Check className="h-3.5 w-3.5 text-ok" /> : <Copy className="h-3.5 w-3.5" />}
              </button>
            </div>
          )}
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
          "inline-flex items-center gap-1.5 rounded-chip px-2 py-1 text-xs font-medium",
          "bg-accent/15 text-accent-ink ring-1 ring-accent/25 transition-colors hover:bg-accent/25",
        )}
      >
        <ArrowUpCircle className="h-3.5 w-3.5" />
        {t("update.available", { latest })}
      </button>
    </div>
  );
}
