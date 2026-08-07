import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ShieldHalf } from "lucide-react";
import { getPostureFacts, type Approval, type Reach } from "@/lib/api";
import { useT, type TFunc } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const REACHES: Reach[] = ["read_only", "workspace", "workspace_shell"];
const APPROVALS: Approval[] = ["always", "suspicious", "never"];

/** One three-value selector. Deliberately not a slider: a slider says these are points on a scale
 *  of "how safe", and they are not — full reach with no questions is a corner of a grid someone
 *  chose, not a place they slid past. */
function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
  render,
  disabled,
}: {
  label: string;
  value: T;
  options: T[];
  onChange: (v: T) => void;
  render: (v: T) => string;
  disabled?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wider text-muted-foreground">{label}</span>
      <div className="flex overflow-hidden rounded-chip border border-border">
        {options.map((opt) => (
          <button
            key={opt}
            type="button"
            disabled={disabled}
            onClick={() => onChange(opt)}
            className={cn(
              "px-2.5 py-1 text-xs transition-colors disabled:opacity-50",
              value === opt
                ? "bg-accent/20 text-accent"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {render(opt)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** Build the status line from FACTS the server reported, one clause at a time.
 *
 *  Every clause is a translated fragment rather than a server-side sentence, so the one line that
 *  most needs to be understood is not the one line left in English. */
function sentence(facts: NonNullable<ReturnType<typeof useFacts>["data"]>, t: TFunc): string {
  const parts = [
    facts.writes === "nothing"
      ? t("code.posture.saysNoWrites")
      : t("code.posture.saysWrites", { path: facts.workspace }),
    t(`code.posture.saysShell.${facts.shell}` as const),
    t(`code.posture.saysPause.${facts.pauses}` as const),
  ];
  return parts.join(" ");
}

function useFacts(reach: Reach, approval: Approval, workspace: string) {
  return useQuery({
    queryKey: ["posture", reach, approval, workspace],
    queryFn: () => getPostureFacts(reach, approval, workspace || null),
    // Never cached across time: the answer depends on whether a Docker daemon is answering RIGHT
    // NOW, and a stale "runs in a container" is the single most misleading thing this can say.
    staleTime: 0,
    gcTime: 0,
  });
}

/** The two axes, and one generated line saying what they mean here.
 *
 *  The framing is Codex's and it is the right one: the sandbox decides what is technically
 *  possible, the approval policy decides when to stop and ask before crossing it. They are
 *  orthogonal, so they are two controls — collapsing them into one "safety level" would make the
 *  diagonal (full reach, no questions) something a user slides past rather than chooses.
 */
export function PostureBar({
  workspace,
  reach,
  approval,
  onReach,
  onApproval,
  disabled,
  compact,
}: {
  workspace: string;
  reach: Reach;
  approval: Approval;
  onReach: (r: Reach) => void;
  onApproval: (a: Approval) => void;
  disabled?: boolean;
  /** Render as one row for the composer strip instead of a titled block. */
  compact?: boolean;
}) {
  const t = useT();
  const facts = useFacts(reach, approval, workspace);

  // Compact: one row beside the composer instead of a 167px block above it.
  //
  // The sentence stays VISIBLE. A first draft moved it into the row's `title` — one hover away —
  // and the test suite caught that as three failures, correctly: this line is what the agent is
  // allowed to do to your files, not a tooltip. It was three wrapped lines only because it lived in
  // a 384px column; across the composer it is one. Hiding it would have been the whole point of the
  // screen (say what is happening) traded for the appearance of tidiness.
  if (compact) {
    return (
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <ShieldHalf className="h-4 w-4 shrink-0 text-accent" />
          <Choice
            label={t("code.posture.reach")}
            value={reach}
            options={REACHES}
            onChange={onReach}
            render={(r) => t(`code.posture.reach.${r}` as const)}
            disabled={disabled}
          />
          <Choice
            label={t("code.posture.approval")}
            value={approval}
            options={APPROVALS}
            onChange={onApproval}
            render={(a) => t(`code.posture.approval.${a}` as const)}
            disabled={disabled}
          />
        </div>
        {facts.data ? (
          <p className="text-xs text-muted-foreground">{sentence(facts.data, t)}</p>
        ) : null}
        {facts.data?.fell_back_to_host ? (
          <p className="flex items-start gap-1.5 text-xs text-bad">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {t("code.posture.fellBack")}
          </p>
        ) : null}
        {facts.isError ? <p className="text-xs text-bad">{t("code.posture.unknown")}</p> : null}
      </div>
    );
  }

  return (
    <div className="space-y-2 border-b border-hairline p-3">
      <div className="flex items-center gap-2 text-accent">
        <ShieldHalf className="h-4 w-4" />
        <h2 className="text-sm font-semibold text-foreground">{t("code.posture.title")}</h2>
      </div>
      <Choice
        label={t("code.posture.reach")}
        value={reach}
        options={REACHES}
        onChange={onReach}
        render={(r) => t(`code.posture.reach.${r}` as const)}
        disabled={disabled}
      />
      <Choice
        label={t("code.posture.approval")}
        value={approval}
        options={APPROVALS}
        onChange={onApproval}
        render={(a) => t(`code.posture.approval.${a}` as const)}
        disabled={disabled}
      />
      {facts.data ? (
        <>
          <p className="text-xs text-muted-foreground">{sentence(facts.data, t)}</p>
          {facts.data.fell_back_to_host ? (
            <p className="flex items-start gap-1.5 text-xs text-bad">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {t("code.posture.fellBack")}
            </p>
          ) : null}
        </>
      ) : facts.isError ? (
        // Silence here would read as "nothing to worry about", which is the opposite of what an
        // unknown posture means.
        <p className="text-xs text-bad">{t("code.posture.unknown")}</p>
      ) : null}
    </div>
  );
}
