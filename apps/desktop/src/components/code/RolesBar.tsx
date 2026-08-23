import { useQuery } from "@tanstack/react-query";
import { Layers } from "lucide-react";
import { ModelPicker } from "@/components/code/ModelPicker";
import { getRoleModels, type Profile } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const PROFILES: Profile[] = ["economy", "balanced", "max"];

/** The four roles a model can be chosen for, in the order the loop runs them. */
export const ROLES = ["explore", "plan", "edit", "review"] as const;
export type Role = (typeof ROLES)[number];

/** Written out rather than built with `code.roles.${role}`.
 *
 *  `i18n.reachable.test.ts` greps for each key as a literal and lists anything it cannot find as
 *  dead. A template hides four keys from it, and the alternative — exempting the whole
 *  `code.roles.` prefix — would hide eleven more that really could go dead unnoticed.
 */
const ROLE_KEY = {
  explore: "code.roles.explore",
  plan: "code.roles.plan",
  edit: "code.roles.edit",
  review: "code.roles.review",
} as const;

/** One slug per role, `""` meaning "whatever the profile resolves to". */
export type RoleOverride = Record<Role, string>;

export const NO_OVERRIDE: RoleOverride = { explore: "", plan: "", edit: "", review: "" };

/** The override in the shape the API takes, or `null` when nothing was overridden.
 *
 *  `null` rather than an object of empty strings on purpose: `resolve()` merges field by field and
 *  reads `None` as "keep the profile's answer", so sending an empty slug would be a request to run
 *  that role on a model named empty-string.
 */
export function toRoleModels(o: RoleOverride): Record<string, string> | null {
  const out: Record<string, string> = {};
  for (const r of ROLES) if (o[r]) out[r] = o[r];
  return Object.keys(out).length ? out : null;
}

/** Which model does what, and the sentence that keeps this a control rather than a claim.
 *
 *  A coding run is made of jobs with different shapes and one model is rarely best at all of them:
 *  localisation is a search, planning and review are judgements, writing the patch is neither. So
 *  this routes by role.
 *
 *  Three honesty constraints are visible in the markup rather than only in the backend.
 *
 *  **Fusion appears on plan and review and nowhere else.** A "fuse" switch on the coding loop would
 *  be a lie: the router sends any turn carrying tool schemas to a single model, and every turn in a
 *  coding loop carries tools, so it would never fire and would report that it had. Planning and
 *  review are the two turns with no tools.
 *
 *  **Verify has no picker, and that is the point.** It runs the user's command. The thing that
 *  decides whether the work was good is the one part with no opinion, which is what makes the rest
 *  measurable at all — a field for it would imply a choice exists.
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
  override,
  onOverride,
  oneModel,
  onOneModel,
}: {
  profile: Profile;
  onProfile: (p: Profile) => void;
  disabled?: boolean;
  /** Render as one row for the composer strip instead of a titled block. */
  compact?: boolean;
  /** Per-role model choice. Omit both and the profile's tiers are shown read-only. */
  override?: RoleOverride;
  onOverride?: (o: RoleOverride) => void;
  /** "One model does all four." Held by the caller, not derived — see the note by `single`. */
  oneModel?: boolean;
  onOneModel?: (v: boolean) => void;
}) {
  const t = useT();
  const roles = useQuery({
    queryKey: ["roles", profile],
    queryFn: () => getRoleModels(profile),
  });

  // Narrowed once. Repeating `override && onOverride` at each use site made TS decide the second
  // half was always true and error on it — and read worse than the thing it was guarding.
  const pick = override && onOverride ? { value: override, set: onOverride } : null;
  // A flag, and the first version's reasoning against one was wrong. Deriving "one model" from the
  // four values cannot work, because THREE intents exist and two of them share the same values:
  // "use the profile's tiers" and "one model, not chosen yet" are both four empty strings. Ticking
  // the box before picking anything therefore did nothing at all — it wrote four blanks and the
  // derivation read them back as "not single", so the box would not even stay ticked.
  //
  // What made a flag unsafe was drift between it and the rows. That is closed by construction here
  // rather than by argument: while it is on, the only control rendered writes all four at once.
  const single = !!oneModel;

  // Resolved server-side: the tiers honour the user's cost mode and per-tier settings, and a second
  // copy of that resolution here would display a model the run does not actually use.
  const resolved: Record<Role, string | null | undefined> = {
    explore: roles.data?.explore,
    plan: roles.data?.plan,
    edit: roles.data?.edit,
    review: roles.data?.review,
  };
  const fused: Partial<Record<Role, boolean>> = {
    plan: !!roles.data?.fuse_plan,
    review: !!roles.data?.fuse_review,
  };

  const picker = (
    <div className="flex overflow-hidden rounded-chip border border-border">
      {PROFILES.map((p) => (
        <button
          key={p}
          type="button"
          // Same fix the worker picker took in rc13, and needed here for the same reason: this is a
          // toggle group, and which one is chosen was said in colour and nowhere else.
          aria-pressed={profile === p}
          disabled={disabled}
          onClick={() => onProfile(p)}
          className={cn(
            "px-2.5 py-1 text-xs transition-colors disabled:opacity-50",
            profile === p
              ? "bg-accent/20 text-accent-ink"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t(`code.roles.profile.${p}` as const)}
        </button>
      ))}
    </div>
  );

  const roleRow = (role: Role) => (
    <div key={role} className="flex items-baseline gap-2">
      <span className="w-20 shrink-0 text-muted-foreground">{t(ROLE_KEY[role])}</span>
      {pick ? (
        // The role's OWN resolved model as the chip's "no choice" reading, and no caption — the row
        // already says which role this is. Two earlier shapes were wrong in opposite directions: the
        // first replaced the resolved slug with an empty field, hiding the information the control
        // exists to change; the second showed both, and the chip's half was the INSTALL default, so
        // `Explorar` read "padrão · deepseek-chat-v3.1" beside the mistral it actually runs on. Two
        // model names on one row, and the prominent one wrong for that role.
        <ModelPicker
          value={pick.value[role]}
          onChange={(slug) => pick.set({ ...pick.value, [role]: slug })}
          fallback={resolved[role] ?? ""}
          label={null}
          disabled={disabled}
        />
      ) : (
        <span className="truncate font-mono text-foreground/80">
          {resolved[role] ?? t("code.roles.default")}
        </span>
      )}
      {fused[role] ? <span className="shrink-0 text-accent-ink">· {t("code.roles.panel")}</span> : null}
    </div>
  );

  const table = (
    <details className="text-xs">
      <summary className="cursor-pointer text-muted-foreground">{t("code.roles.show")}</summary>
      <div className="mt-1.5 space-y-1">
        {pick && single ? (
          // One picker, not four showing the same slug. Four would invite editing one of them, which
          // silently leaves this state while the checkbox above still says "one model".
          <div className="flex items-baseline gap-2">
            <span className="w-20 shrink-0 text-muted-foreground">{t("code.roles.everyStep")}</span>
            <ModelPicker
              value={pick.value.explore}
              onChange={(slug) =>
                pick.set(Object.fromEntries(ROLES.map((r) => [r, slug])) as RoleOverride)
              }
              label={null}
              disabled={disabled}
            />
          </div>
        ) : (
          ROLES.map(roleRow)
        )}
        <div className="flex items-baseline gap-2">
          <span className="w-20 shrink-0 text-muted-foreground">{t("code.roles.verify")}</span>
          {/* The one row with no model, and the reason the rest can be measured at all: the thing
              that decides whether the work was good is the part with no opinion. */}
          <span className="text-muted-foreground">{t("code.roles.verifyNote")}</span>
        </div>
        {pick ? (
          <label className="flex items-center gap-2 pt-1 text-muted-foreground">
            <input
              type="checkbox"
              checked={single}
              disabled={disabled}
              onChange={(e) => {
                onOneModel?.(e.target.checked);
                // Collapse to whatever was already picked, so ticking the box never silently
                // discards a choice — and untick clears, because four copies of one slug left
                // behind would read as four deliberate per-role picks.
                pick.set(
                  e.target.checked
                    ? (Object.fromEntries(
                        ROLES.map((r) => [r, pick.value.explore || pick.value.edit || ""]),
                      ) as RoleOverride)
                    : NO_OVERRIDE,
                );
              }}
            />
            {t("code.roles.oneModel")}
          </label>
        ) : null}
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
    <div className="space-y-2">
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
