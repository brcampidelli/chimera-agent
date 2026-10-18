import { useId, useMemo, type ReactNode } from "react";
import { BadgeCheck, Loader2, Plug, Plus, Puzzle, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/panel";
import { focusRing } from "@/components/ui/focus";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { CapabilityLine } from "@/components/catalog/CapabilityLine";
import { StatusPill } from "@/components/catalog/StatusPill";
import { badgeOf, type CatalogItem, type CatalogKind } from "@/components/catalog/model";

const KIND_ICON: Record<CatalogKind, typeof Puzzle> = { skill: Sparkles, connector: Plug, plugin: Puzzle };

/** One or two letters from the name. No third-party logo: a picture taken from a stranger's package
 *  is one more thing to vouch for, and a monogram in tokens follows the theme and the contrast. */
export function monogram(name: string): string {
  const words = name.replace(/[^\p{L}\p{N}]+/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  const letters = words.length > 1 ? words[0][0] + words[1][0] : words[0].slice(0, 2);
  return letters.toUpperCase();
}

/** "★ 1.2K · GitHub, 12/10" — the date goes with the number so it is never read as live. */
function useStars(stars: number | null, at: string | null): string | null {
  const { lang, t } = useI18n();
  return useMemo(() => {
    if (stars === null) return null;
    const n = new Intl.NumberFormat(lang, { notation: "compact", maximumFractionDigits: 1 }).format(stars);
    const time = at ? Date.parse(at) : Number.NaN;
    const date = Number.isNaN(time)
      ? "—"
      : new Intl.DateTimeFormat(lang, { day: "2-digit", month: "2-digit", timeZone: "UTC" }).format(new Date(time));
    return t("catalogUi.stars", { n, date });
  }, [stars, at, lang, t]);
}

/**
 * The catalog card (study 04, §3).
 *
 * What it says, in order: the name and the badge it earned by rule; the description; what it would
 * RUN, before who made it; then author, licence and stars when fetched. Never an install count.
 *
 * The whole card opens the detail through a button stretched over it from the title, so the card is
 * one tab stop for "open" and the action on the right keeps its own stop above it.
 */
export function CatalogCard({
  item,
  onOpen,
  onInstall,
  installing = false,
  why,
  installLabel,
  action,
  note,
}: {
  item: CatalogItem;
  /** Opens the detail. Without it the title is plain text. */
  onOpen?: (item: CatalogItem) => void;
  /** Installs SWITCHED OFF. Without it the card shows no install action. */
  onInstall?: (item: CatalogItem) => void;
  installing?: boolean;
  /** Only in "For you": the local reason it is suggested. */
  why?: string;
  /** The kind's own name for the install button, in both states; the reason still describes it. */
  installLabel?: string;
  /** A control of the kind's own beside the state — a plugin's switch. Drawn above the stretched
   *  button, so it keeps its own click and its own tab stop. */
  action?: ReactNode;
  /** One line under the meta for what the kind needs said on the card: "Files: +1 −0 ~0". */
  note?: ReactNode;
}) {
  const { t } = useI18n();
  const base = useId();
  const ids = { desc: `${base}-desc`, cap: `${base}-cap`, status: `${base}-status`, refusal: `${base}-refusal` };
  const badge = badgeOf(item);
  const stars = useStars(item.stars, item.stars_at);
  const KindIcon = KIND_ICON[item.kind];
  const describedBy = [ids.desc, ids.cap, item.status ? ids.status : null].filter(Boolean).join(" ");
  const meta: ReactNode[] = [];
  if (item.author) meta.push(<span key="by">{t("catalogUi.by", { author: item.author })}</span>);
  if (item.source)
    meta.push(
      <span key="source" className="truncate font-mono">
        {item.source}
      </span>,
    );
  if (stars) meta.push(<span key="stars">{stars}</span>);

  return (
    <article
      aria-label={item.name}
      aria-describedby={describedBy}
      className="surface relative flex gap-3 p-4 transition-colors duration-1 ease-out hover:bg-surface-hover"
    >
      <span
        aria-hidden
        className="relative flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface-2 text-sm font-semibold text-muted-foreground ring-1 ring-hairline"
      >
        {monogram(item.name)}
        <KindIcon className="absolute -bottom-1 -right-1 h-3 w-3 rounded-sm bg-card" />
      </span>

      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex min-w-0 items-center gap-1.5">
          <h4 className="truncate text-sm font-semibold">
            {onOpen ? (
              <button
                type="button"
                onClick={() => onOpen(item)}
                className={cn("text-left after:absolute after:inset-0", focusRing)}
              >
                {item.name}
              </button>
            ) : (
              item.name
            )}
          </h4>
          {badge === "vendor" && (
            <span className="inline-flex shrink-0 items-center text-accent-ink" data-badge="vendor">
              <BadgeCheck aria-hidden className="h-3.5 w-3.5" />
              <span className="sr-only">{t("catalogUi.badge.vendor")}</span>
            </span>
          )}
          {badge === "anthropic" && <Badge tone="muted">{t("catalogUi.badge.anthropic")}</Badge>}
          {badge === "chimera" && <Badge tone="accent">{t("catalogUi.badge.chimera")}</Badge>}
        </div>
        <p id={ids.desc} className="line-clamp-2 text-sm text-muted-foreground">
          {item.description}
        </p>
        <CapabilityLine id={ids.cap} facts={item.capabilities} />
        {why && <p className="text-xs text-accent-ink">{why}</p>}
        <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
          {meta.map((part, i) => (
            <span key={i} className="inline-flex min-w-0 items-center gap-1.5">
              {i > 0 && <span aria-hidden>·</span>}
              {part}
            </span>
          ))}
          {item.license ? (
            <Badge tone={item.license.permissive ? "muted" : "warn"}>{item.license.spdx}</Badge>
          ) : (
            <Badge tone="warn">{t("catalogUi.licenseUnread")}</Badge>
          )}
        </div>
        {note}
        {!item.installable && item.refusal && !item.status && (
          <p id={ids.refusal} className="text-xs text-warn-foreground">
            {item.refusal}
          </p>
        )}
      </div>

      {/* Above the stretched button, so it keeps its own click and its own tab stop. */}
      <div className="relative z-10 flex shrink-0 flex-col items-end gap-2">
        {item.status ? (
          <StatusPill id={ids.status} status={item.status} />
        ) : onInstall ? (
          <button
            type="button"
            onClick={() => onInstall(item)}
            disabled={!item.installable || installing}
            aria-describedby={!item.installable && item.refusal ? ids.refusal : undefined}
            aria-label={
              installing
                ? t("catalogUi.installing", { name: item.name })
                : (installLabel ??
                  (item.installable
                    ? t("catalogUi.install", { name: item.name })
                    : t("catalogUi.cannotInstall", { name: item.name })))
            }
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-card text-foreground transition-colors duration-1 ease-out hover:bg-surface-hover disabled:cursor-not-allowed disabled:opacity-50",
              focusRing,
            )}
          >
            {installing ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Plus aria-hidden className="h-4 w-4" />}
          </button>
        ) : null}
        {action}
      </div>
    </article>
  );
}
