import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, Package, Search, Trash2 } from "lucide-react";

import {
  getSkillBundles,
  getSkillCatalog,
  installSkillBundle,
  setSkillBundleStatus,
  uninstallSkillBundle,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, Panel, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { useT } from "@/lib/i18n";
import type { CatalogEntry } from "@/lib/types";

/**
 * Skills you can install — other people's, fetched from their repositories on request.
 *
 * Two things this screen refuses to do, and they are the design.
 *
 * It does not show eighty names in one flat list. These were written for a different agent, and
 * most do not transfer unchanged: some want a server running beside them, some want a GPU or a
 * full LaTeX install, five run only on macOS, and twenty talk to a runtime that is not ours. A
 * plain list would advertise eighty working features and deliver rather fewer — and the person
 * who found that out would find it out after downloading. So the verdict travels next to the
 * name, before the button.
 *
 * And it does not switch anything on. An installed skill's instructions reach the agent's prompt
 * and can tell it to run the scripts that came in the same directory, which is the standing of an
 * instruction the owner wrote — from somebody the owner has never met. Downloading and consenting
 * are two clicks here because they are two decisions.
 */

/** How a rating reads at a glance. Only `native` is good news; the rest are caveats, and a caveat
 *  rendered in the same colour as a pass is a caveat nobody sees. */
const TONES: Record<string, "ok" | "accent" | "warn" | "muted"> = {
  native: "ok",
  needs_setup: "accent",
  needs_service: "warn",
  needs_heavy: "warn",
  os_locked: "warn",
  needs_adaptation: "muted",
};

function Row({ entry }: { entry: CatalogEntry }) {
  const t = useT();
  const client = useQueryClient();
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["skill-catalog"] });
    void client.invalidateQueries({ queryKey: ["skill-bundles"] });
  };

  const install = useMutation({ mutationFn: () => installSkillBundle(entry.name), onSuccess: refresh });
  const toggle = useMutation({
    mutationFn: (on: boolean) => setSkillBundleStatus(entry.name, on ? "active" : "inactive"),
    onSuccess: refresh,
  });
  const remove = useMutation({ mutationFn: () => uninstallSkillBundle(entry.name), onSuccess: refresh });

  const state = entry.installed ?? "";
  const on = state === "active";
  const rating = entry.portability ?? "native";
  const failed = install.error ?? toggle.error ?? remove.error;

  return (
    <div className="flex flex-col gap-2 border-b border-hairline py-3 last:border-0 sm:flex-row sm:items-start">
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm text-foreground">{entry.name}</span>
          <Badge tone={TONES[rating] ?? "muted"}>{t(`catalog.portability.${rating}`)}</Badge>
          {/* The licence is a chip and not a footnote because these are downloads: an entry whose
              terms we could not read is not the same as a permissive one, and the difference
              belongs where the decision is made. */}
          <Badge tone={entry.permissive ? "muted" : "warn"}>
            {entry.license || t("catalog.noLicense")}
          </Badge>
          {state && state !== "active" ? (
            <Badge tone={state === "pending" ? "warn" : "muted"}>{t(`catalog.state.${state}`)}</Badge>
          ) : null}
        </div>

        <p className="text-xs text-muted-foreground">{entry.description}</p>

        {/* Said before the button, not after the download: this is the list that decides whether
            the thing will run at all on this machine. */}
        {entry.requires && entry.requires.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            {t("catalog.needs")}{" "}
            <span className="font-mono">{entry.requires.join(", ")}</span>
          </p>
        ) : null}
        {/* The reason, in the reader's language. What arrives from the catalogue is the DETAIL
            — "macOS", "a GPU and the model weights" — and the sentence around it is translated
            here, because this line is the most decision-relevant text on the row and an app that
            speaks ten languages was printing it in one. A rating with no template falls back to
            the bare detail rather than to nothing. */}
        {entry.note ? (
          <p className="text-xs text-warn-foreground">
            {t(`catalog.reason.${rating}`) === `catalog.reason.${rating}`
              ? entry.note
              : t(`catalog.reason.${rating}`, { detail: entry.note })}
          </p>
        ) : null}
        {/* Measured from the skill's own text, and said before the download rather than found
            after it. Separate from the verdict above on purpose: that one is a judgement about
            whether the skill works here, and this is a fact about what it calls. A skill can be
            perfectly usable and still mention a tool we lack in an optional branch. */}
        {entry.missing_tools && entry.missing_tools.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            {t("catalog.mentions")}{" "}
            <span className="font-mono">{entry.missing_tools.join(", ")}</span>
          </p>
        ) : null}
        {entry.author ? (
          // Several of these are ports of somebody else's work and say so upstream. Carrying the
          // field means the credit reaches a reader instead of stopping at the repository.
          <p className="text-xs text-muted-foreground">{t("catalog.by", { author: entry.author })}</p>
        ) : null}
        {failed ? (
          <p className="text-xs text-bad-foreground">{failed instanceof Error ? failed.message : String(failed)}</p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-2">
        <a
          href={entry.homepage ?? "#"}
          target="_blank"
          rel="noreferrer"
          className="text-muted-foreground hover:text-foreground"
          title={t("catalog.readSource")}
          aria-label={t("catalog.readSourceOf", { name: entry.name })}
        >
          <ExternalLink className="h-4 w-4" />
        </a>

        {!state ? (
          <Button size="sm" variant="outline" disabled={install.isPending} onClick={() => install.mutate()}>
            {install.isPending ? <Spinner /> : <Download className="h-4 w-4" />}
            {t("catalog.install")}
          </Button>
        ) : (
          <>
            <Button
              size="sm"
              variant={on ? "primary" : "outline"}
              disabled={toggle.isPending}
              onClick={() => toggle.mutate(!on)}
            >
              {on ? t("catalog.on") : t("catalog.off")}
            </Button>
            <button
              type="button"
              aria-label={t("catalog.uninstallOf", { name: entry.name })}
              title={t("catalog.uninstall")}
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
              className="text-muted-foreground hover:text-bad"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}

export function SkillCatalog() {
  const t = useT();
  const [query, setQuery] = useState("");
  const catalog = useQuery({ queryKey: ["skill-catalog"], queryFn: getSkillCatalog });
  // Fetched alongside so the list re-renders when a bundle is switched from anywhere else.
  useQuery({ queryKey: ["skill-bundles"], queryFn: getSkillBundles });

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = (catalog.data ?? []).filter(
      (e) =>
        !needle ||
        (e.name ?? "").toLowerCase().includes(needle) ||
        (e.description ?? "").toLowerCase().includes(needle),
    );
    const byTopic = new Map<string, CatalogEntry[]>();
    for (const entry of matched) {
      const key = entry.topic || "other";
      byTopic.set(key, [...(byTopic.get(key) ?? []), entry]);
    }
    return [...byTopic.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [catalog.data, query]);

  const installed = (catalog.data ?? []).filter((e) => e.installed).length;

  if (catalog.isLoading) return <Panel title={t("catalog.title")}><Spinner /></Panel>;
  if (catalog.error) return <ErrorState error={catalog.error} onRetry={() => void catalog.refetch()} />;

  return (
    <Panel title={t("catalog.title")}>
      {/* `Panel` insets its TITLE BAR by `px-4` and gives its body nothing, so a panel whose content
          forgets to pad sits flush against the border while its own heading does not — a 16px
          disagreement inside one card. Measured here before the fix: title 17px from the edge,
          first paragraph 1px.

          The body is padded here rather than in `Panel`, because the divider lines in a LIST panel
          are meant to run edge to edge and padding the wrapper would inset those too. */}
      <div className="px-4 py-3">
      <p className="mb-3 text-xs text-muted-foreground">
        {t("catalog.subtitle", { n: catalog.data?.length ?? 0, installed })}
      </p>
      <div className="mb-3 flex items-center gap-2">
        <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("catalog.search")}
          aria-label={t("catalog.search")}
          className="w-full rounded-card border border-hairline bg-surface-2/40 p-2 text-sm text-foreground placeholder:text-muted-foreground"
        />
      </div>

      {/* Where they come from, and on whose terms — once, at the top, rather than repeated on
          every row. Nothing here ships with the app. */}
      <p className="mb-4 flex items-start gap-2 text-xs text-muted-foreground">
        <Package className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {t("catalog.provenance")}
      </p>

      {groups.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("catalog.noMatch", { q: query })}</p>
      ) : (
        groups.map(([topic, entries]) => (
          <section key={topic} className="mb-5">
            <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              {topic} <span className="font-normal">({entries.length})</span>
            </h3>
            {entries.map((entry) => (
              <Row key={entry.name} entry={entry} />
            ))}
          </section>
        ))
      )}
      </div>
    </Panel>
  );
}
