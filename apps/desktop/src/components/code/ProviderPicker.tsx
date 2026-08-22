import { useQuery } from "@tanstack/react-query";

import { getDoctor } from "@/lib/api";
import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";

/**
 * Who does the work: Chimera's own loop, or an agent you already trust.
 *
 * The list comes from `doctor`, not from a constant here, and that is the whole design of this
 * control. Availability is resolved on the machine the sidecar is running on — which for a packaged
 * build is a machine nobody looked at, assembled by CI. A hard-coded list would offer Claude Code to
 * someone without Node and then fail with "FileNotFoundError: npx", which reads as a bug in us.
 *
 * An agent that is present-but-not-runnable is shown DISABLED with its install line in the tooltip —
 * "Gemini needs npm i -g" is useful next to a Claude Code you can actually press. But when NOTHING is
 * runnable the whole row disappears, because the composer is the most valuable strip in the app and a
 * permanently greyed control there is clutter for a feature the user cannot reach. Discoverability
 * for that case lives in `chimera doctor`, which reports every agent, its availability and how to
 * install it — the right home for "here is a capability you do not have yet".
 */
export function ProviderPicker({
  value,
  onChange,
  disabled,
}: {
  /** "" for Chimera's own loop. */
  value: string;
  onChange: (provider: string) => void;
  disabled?: boolean;
}) {
  const t = useT();
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor });
  const agents = doctor.data?.external_agents ?? [];

  // Nothing installed and nothing selected: no control. An empty picker offering one option is a
  // decision presented as a choice.
  if (agents.length === 0 || (!agents.some((a) => a.available) && !value)) return null;

  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wider text-muted-foreground">
        {t("code.provider.label")}
      </span>
      <div className="flex overflow-hidden rounded-chip border border-border">
        <button
          type="button"
          // Which one is chosen was said in colour and nowhere else, so a screen reader heard
          // three buttons and no answer to "which is active". This is a toggle group; pressed is
          // the state it has.
          aria-pressed={value === ""}
          disabled={disabled}
          onClick={() => onChange("")}
          className={cn(
            "px-2.5 py-1 text-xs transition-colors duration-1 ease-out disabled:opacity-50",
            focusRing,
            value === ""
              ? "bg-accent/20 text-accent"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {t("code.provider.native")}
        </button>
        {agents.map((agent) => (
          <Tooltip
            key={agent.key}
            label={agent.available ? agent.notes : t("code.provider.missing", { hint: agent.install_hint })}
          >
            <button
              type="button"
              aria-pressed={value === agent.key}
              // `aria-disabled` rather than `disabled` for an agent that is merely missing.
              //
              // The tooltip beside this already carries the reason — "not installed here, run
              // this to get it" — and a `disabled` button takes no focus, so that reason reached
              // a pointer and nobody else. Marked this way the button is still reachable, still
              // announces that it cannot be used, and the sentence explaining WHY is finally
              // available to someone navigating by keyboard.
              //
              // `disabled` stays real for the other case: a turn in flight is not a missing
              // install, and there is nothing to explain there.
              aria-disabled={!agent.available || undefined}
              disabled={disabled}
              onClick={() => agent.available && onChange(agent.key)}
              className={cn(
                "px-2.5 py-1 text-xs transition-colors duration-1 ease-out disabled:opacity-40",
                focusRing,
                !agent.available && "cursor-not-allowed opacity-40",
                value === agent.key
                  ? "bg-accent/20 text-accent"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {agent.label}
            </button>
          </Tooltip>
        ))}
      </div>
    </div>
  );
}
