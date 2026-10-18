import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { BookOpen, ExternalLink, Trash2 } from "lucide-react";

import { getSkillBundleText, uninstallSkillBundle } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ErrorState } from "@/components/ui/async";
import { Badge, Spinner } from "@/components/ui/panel";
import { day, shortSha } from "@/components/SkillsActiveNow";
import { useT } from "@/lib/i18n";
import type { SkillBundle } from "@/lib/types";
import { errorText, useSkillRefresh } from "@/components/skills/SkillParts";

export const PRE = "max-h-96 overflow-y-auto whitespace-pre-wrap wrap-break-word rounded-lg bg-surface-2 p-2 text-xs text-muted-foreground";

/** A label and its value, in the panel's two-column list. */
export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="col-span-2 min-w-0 wrap-break-word">{children}</dd>
    </>
  );
}

export function Facts({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-xs">{children}</dl>;
}

/** "What reaches the agent" — the question the panel exists to answer, under its own heading. */
export function Reaches({ children }: { children: ReactNode }) {
  const t = useT();
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{t("skills.detail.reaches")}</h3>
      {children}
    </section>
  );
}

/** The chip as the catalogue drew it: an entry whose terms nobody could read is not the same as a
 *  permissive one, and these are downloads — the difference belongs where the decision is made. */
export function LicenceChip({ license, permissive }: { license: string | null | undefined; permissive: boolean }) {
  const t = useT();
  return <Badge tone={permissive ? "muted" : "warn"}>{license || t("catalog.noLicense")}</Badge>;
}

export function SourceLink({ href, name }: { href: string; name: string }) {
  const t = useT();
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex items-center gap-1 text-accent-ink hover:underline"
      aria-label={t("catalog.readSourceOf", { name })}
    >
      {t("catalog.readSource")}
      <ExternalLink aria-hidden className="h-3.5 w-3.5" />
    </a>
  );
}

/**
 * The installed SKILL.md, as text rather than rendered — what the owner reads should be what the
 * agent would read, not a prettier document. Counted as read once the text is on the screen, not
 * when the button is pressed: a panel that failed to load showed nothing to consent to.
 */
export function ReadText({ name, onRead }: { name: string; onRead: (name: string) => void }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const text = useQuery({
    queryKey: ["skill-bundle-text", name],
    queryFn: () => getSkillBundleText(name),
    enabled: open,
  });
  const shown = text.data?.name;
  useEffect(() => {
    if (open && shown === name) onRead(name);
  }, [open, shown, name, onRead]);
  return (
    <div className="space-y-2">
      <Button size="sm" variant="outline" aria-label={t("skills.upload.readOf", { name })} onClick={() => setOpen((v) => !v)}>
        <BookOpen className="h-4 w-4" />
        {t("skills.upload.read")}
      </Button>
      {open ? (
        text.isError ? (
          <ErrorState error={text.error} onRetry={() => void text.refetch()} />
        ) : text.isLoading || !text.data ? (
          <Spinner />
        ) : (
          <>
            <pre className={PRE}>{text.data.text}</pre>
            {text.data.truncated ? <p className="text-xs text-warn-foreground">{t("skills.upload.truncated")}</p> : null}
          </>
        )
      ) : null}
    </div>
  );
}

export function Uninstall({ name, onRemoved }: { name: string; onRemoved: () => void }) {
  const t = useT();
  const refresh = useSkillRefresh();
  const remove = useMutation({
    mutationFn: () => uninstallSkillBundle(name),
    onSuccess: () => {
      refresh();
      onRemoved();
    },
  });
  return (
    <div className="space-y-1">
      <Button size="sm" variant="ghost" disabled={remove.isPending} aria-label={t("catalog.uninstallOf", { name })} onClick={() => remove.mutate()}>
        <Trash2 className="h-4 w-4" />
        {t("catalog.uninstall")}
      </Button>
      {remove.error ? <p className="text-xs text-bad-foreground">{errorText(remove.error)}</p> : null}
    </div>
  );
}

export function Commit({ bundle }: { bundle: SkillBundle | undefined }) {
  const t = useT();
  // Which bytes are on disk. The catalogue only names a branch; the install recorded the commit it
  // resolved to, and that is what an update is measured against.
  if (!bundle?.ref || !/^[0-9a-f]{40}$/i.test(bundle.ref)) return null;
  return (
    <p className="font-mono">
      {t("skills.active.commit", { sha: shortSha(bundle.ref) })}
      {bundle.committed_at ? ` · ${day(bundle.committed_at)}` : ""}
    </p>
  );
}
