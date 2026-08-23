import { useQuery } from "@tanstack/react-query";
import { Layers } from "lucide-react";
import { getRoleModels, type Profile } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const PROFILES: Profile[] = ["economy", "balanced", "max"];

/** Which model does what, and the sentence that keeps this a control rather than a claim.
 *
 *  A coding run is made of jobs with different shapes and one model is rarely best at all of them:
 *  localisation is a search, planning and review are judgements, writing the patch is neither. So
 *  this routes by role.
 *
 *  Two honesty constraints are visible in the markup rather than only in the backend.
 *
 *  **Fusion appears on plan and review and nowhere else.** A "fuse" switch on the coding loop would
 *  be a lie: the router sends any turn carrying tool schemas to a single model, and every turn in a
 *  coding loop carries tools, so it would never fire and would report that it had. Planning and
 *  review are the two turns with no tools.
 *
 *  **The note at the bottom is not a disclaimer, it is the status.** Routing has not been measured
 *  yet (`bench/role_routing/PREREGISTRATION.md` is written and unfunded), and every competitor that
 *  claims something like this is also unmeasured. Shipping the selector without saying so would put
 *  this project back in exactly the company it spent a month getting out of.
 */
export function RolesBar({
  profile,
  onProfile,
  disabled,
  compact,
}: {
  profile: Profile;
  onProfile: (p: Profile) => void;
  disabled?: boolean;
  /** Render as one row for the composer strip instead of a titled block. */
  compact?: boolean;
}) {
  const t = useT();
  const roles = useQuery({
    queryKey: ["roles", profile],
    queryFn: () => getRoleModels(profile),
  });

  // Resolved server-side: the tiers honour the user's cost mode and per-tier settings, and a second
  // copy of that resolution here would display a model the run does not actually use.
  const rows: [string, string | null | undefined, boolean][] = [
    [t("code.roles.explore"), roles.data?.explore, false],
    [t("code.roles.plan"), roles.data?.plan, !!roles.data?.fuse_plan],
    [t("code.roles.edit"), roles.data?.edit, false],
    [t("code.roles.review"), roles.data?.review, !!roles.data?.fuse_review],
  ];

  const picker = (
    <div className="flex overflow-hidden rounded-chip border border-border">
      {PROFILES.map((p) => (
        <button
          key={p}
          type="button"
          // Same fix the worker picker took in rc13, and it was needed here for the same reason:
          // this is a toggle group, and which one is chosen was said in colour and nowhere else.
          // It stayed invisible because nothing rendered this control at all — surfacing it is
          // what surfaced the gap.
          aria-pressed={profile === p}
          disabled={disabled}
          onClick={() => onProfile(p)}
          className={cn(
            "px-2.5 py-1 text-xs transition-colors disabled:opacity-50",
            profile === p ? "bg-accent/20 text-accent" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(`code.roles.profile.${p}` as const)}
        </button>
      ))}
    </div>
  );

  const table = (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground">{t("code.roles.show")}</summary>
      <div className="mt-1.5 space-y-1">
        {rows.map(([label, model, fused]) => (
          <div key={label} className="flex items-baseline gap-2">
            <span className="w-20 shrink-0 text-muted-foreground">{label}</span>
            <span className="truncate font-mono text-foreground/80">
              {model ?? t("code.roles.default")}
            </span>
            {fused ? <span className="shrink-0 text-accent">· {t("code.roles.panel")}</span> : null}
          </div>
        ))}
        <div className="flex items-baseline gap-2">
          <span className="w-20 shrink-0 text-muted-foreground">{t("code.roles.verify")}</span>
          {/* The one row with no model, and the reason the rest can be measured at all: the thing
              that decides whether the work was good is the part with no opinion. */}
          <span className="text-muted-foreground">{t("code.roles.verifyNote")}</span>
        </div>
      </div>
    </details>
  );

  // Compact: the picker sits beside the composer like a model selector. `unproven` stays VISIBLE
  // rather than moving into the disclosure — it is the status of the feature, not a footnote, and a
  // selector that offers three profiles while hiding "this has not been shown to help" is the exact
  // claim-without-a-number this project keeps refusing to make.
  if (compact) {
    return (
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-3">
          <Layers className="h-4 w-4 shrink-0 text-accent" />
          {picker}
          {table}
        </div>
        <p className="text-xs text-muted-foreground">{t("code.roles.unproven")}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2 border-b border-hairline p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Layers className="h-4 w-4 text-accent" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.roles.title")}</h2>
        <div className="ml-auto">{picker}</div>
      </div>
      {table}
      <p className="text-xs text-muted-foreground">{t("code.roles.unproven")}</p>
    </div>
  );
}
