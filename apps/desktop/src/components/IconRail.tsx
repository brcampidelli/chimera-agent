import type { ComponentType, CSSProperties } from "react";
import {
  Brain,
  Clock,
  ListChecks,
  FileCode2,
  Gauge,
  PencilRuler,
  Settings as SettingsIcon,
  Moon,
  Sun,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { BrandMark } from "@/components/BrandMark";
import { focusRing } from "@/components/ui/focus";
import { Tooltip } from "@/components/ui/tooltip";
import { useT } from "@/lib/i18n";

/**
 * Every destination, as data.
 *
 * The type used to be a bare union, which the router cannot check a URL against — a hash arrives as
 * a string and something has to say whether it names a real screen. Deriving the type FROM the list
 * keeps one source of truth: adding a screen to the array is what makes it a valid `View`, and a
 * second hand-maintained list cannot drift out of step with the first.
 */
export const VIEWS = [
  "knowledge",
  "automation",
  "work",
  "code",
  "edit",
  "maturity",
  "settings",
] as const;

export type View = (typeof VIEWS)[number];

/** Maturity reports the Chimera project's OWN test coverage, so it needs a source checkout — in
 *  an installed build it renders an empty state. Shipping a permanently blank screen is a trust
 *  cost with no upside, so it appears only in development. */
const NAV: { view: View; labelKey: string; icon: ComponentType<{ className?: string }> }[] = [
  // Ordered by how often a person reaches for it: talk to it, watch it work, open the code, ask
  // what it knows, set up what it does unprompted.
  // Código first: it is where the work starts. Trabalho is where you go to look at what a run DID,
  // which is a second step by definition — and the rail's order is the app's opinion about that.
  { view: "code", labelKey: "nav.code", icon: FileCode2 },
  // Second, next to the conversation and not inside it: asking and editing are different postures,
  // and the screen that begins with "what do you want done" cannot also begin with a file tree.
  { view: "edit", labelKey: "nav.edit", icon: PencilRuler },
  { view: "work", labelKey: "nav.work", icon: ListChecks },
  { view: "knowledge", labelKey: "nav.knowledge", icon: Brain },
  { view: "automation", labelKey: "nav.automation", icon: Clock },
  ...(import.meta.env.DEV
    ? [{ view: "maturity" as const, labelKey: "nav.maturity", icon: Gauge }]
    : []),
];

function RailButton({
  active,
  label,
  onClick,
  icon: Icon,
  igniteIndex,
  isPage = true,
}: {
  active: boolean;
  label: string;
  onClick: () => void;
  icon: ComponentType<{ className?: string }>;
  /** Position in the launch stagger. Absent means this button doesn't take part. */
  igniteIndex?: number;
  /** False for controls that don't navigate (the theme toggle), so aria-current stays meaningful. */
  isPage?: boolean;
}) {
  return (
    // A real tooltip rather than `title=`: the native one never appears for a keyboard user and has
    // an uncontrollable delay. In a rail that is nothing but icons, that gap covered the whole of
    // the app's primary navigation. `aria-label` stays — a tooltip is a visual affordance, not a name.
    <Tooltip label={label}>
      <button
        onClick={onClick}
        aria-label={label}
        // Without this a screen-reader user can hear which button is focused but not which view
        // they are actually looking at.
        aria-current={isPage && active ? "page" : undefined}
        {...(igniteIndex !== undefined && {
          "data-ignite": "icon",
          style: { "--i": igniteIndex } as CSSProperties,
        })}
        className={cn(
          "relative flex h-11 w-11 items-center justify-center rounded-xl2",
          "transition-all duration-1 ease-out",
          // The app's primary navigation had no focus indicator at all: a keyboard user could tab
          // through fifteen destinations with nothing on screen telling them where they were.
          focusRing,
          active
            ? "bg-accent/15 text-accent-ink shadow-rail-active"
            : "text-muted-foreground hover:bg-surface-hover hover:text-foreground",
        )}
      >
        <Icon className="h-5 w-5" />
      </button>
    </Tooltip>
  );
}

export function IconRail({
  view,
  onSelect,
  dark,
  onToggleTheme,
  ignite = false,
}: {
  view: View;
  onSelect: (v: View) => void;
  dark: boolean;
  onToggleTheme: () => void;
  /** Whether the launch sequence is running; drives the per-icon stagger. */
  ignite?: boolean;
}) {
  const t = useT();
  return (
    <div
      {...(ignite && { "data-ignite": "rail" })}
      className="flex w-14 shrink-0 flex-col items-center gap-1 border-r border-hairline bg-card/40 py-3"
    >
      <BrandMark className="mb-2 h-8 w-8" glow {...(ignite && { "data-ignite": "mark" })} />
      <nav className="flex flex-1 flex-col items-center gap-1.5">
        {NAV.map((n, i) => (
          <RailButton
            key={n.view}
            active={view === n.view}
            label={t(n.labelKey)}
            icon={n.icon}
            onClick={() => onSelect(n.view)}
            // 40ms apart. A stagger reads as one sweep rather than N separate arrivals.
            igniteIndex={ignite ? i : undefined}
          />
        ))}
      </nav>
      <div className="flex flex-col items-center gap-1.5">
        <RailButton
          active={false}
          isPage={false}
          label={dark ? t("theme.light") : t("theme.dark")}
          icon={dark ? Sun : Moon}
          onClick={onToggleTheme}
        />
        <RailButton
          active={view === "settings"}
          label={t("nav.settings")}
          icon={SettingsIcon}
          onClick={() => onSelect("settings")}
        />
      </div>
    </div>
  );
}
