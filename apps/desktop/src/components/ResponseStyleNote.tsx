import { useT } from "@/lib/i18n";

/**
 * Where the response style lives. It is chosen per conversation, in the composer, so Settings and the
 * Profile only say so and point there: a second, account-wide copy would be a preference the agent
 * never reads.
 */
export function ResponseStyleNote() {
  const t = useT();
  return (
    <div className="px-4 py-3">
      <div className="text-sm font-medium">{t("code.style.label")}</div>
      <p className="text-xs text-muted-foreground">{t("profile.style.contextual")}</p>
      <a href="#/code" className="text-sm text-accent-ink hover:underline">
        {t("profile.style.composer")}
      </a>
    </div>
  );
}
