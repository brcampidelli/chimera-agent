import { useId, useRef } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { SkillBundle } from "@/lib/types";
import { CatalogDetail, DetailSteps, type StepState } from "@/components/catalog/CatalogDetail";
import type { CatalogItem } from "@/components/catalog/model";
import { BundleSwitch, LearnedActions } from "@/components/skills/SkillParts";
import { BundleBody, EntryBody, LearnedBody, LibraryBody, PinnedBody } from "@/components/skills/SkillDetailBodies";
import type { SkillTarget } from "@/components/skills/targets";

/** Whether an installed skill still waits for its owner to read it before switching on. */
export function isUnread(bundle: Pick<SkillBundle, "name" | "status" | "origin">, read: ReadonlySet<string>): boolean {
  return bundle.origin === "upload" && bundle.status === "pending" && !read.has(bundle.name ?? "");
}

/**
 * Install → Read → Switch on, as separate steps because they are separate decisions (§2.7). For a
 * skill the review is reading the text that will reach the prompt; switching on stays the owner's
 * route, which the bridge never reaches, whatever this draws.
 */
function BundleSteps({
  bundle,
  read,
  item,
  onInstall,
  installing,
}: {
  bundle: Pick<SkillBundle, "name" | "status" | "origin"> | undefined;
  read: ReadonlySet<string>;
  item: CatalogItem;
  onInstall?: (item: CatalogItem) => void;
  installing: boolean;
}) {
  const t = useT();
  const reasonId = useId();
  if (!bundle) {
    return (
      <DetailSteps
        steps={[
          {
            id: "install",
            label: t("catalogUi.step.install"),
            state: "current",
            note: item.installable ? null : <span id={reasonId}>{item.refusal}</span>,
            action: onInstall ? (
              <Button
                size="sm"
                disabled={!item.installable || installing}
                aria-describedby={item.installable ? undefined : reasonId}
                onClick={() => onInstall(item)}
              >
                {installing ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : null}
                {t("catalog.install")}
              </Button>
            ) : null,
          },
          { id: "review", label: t("catalogUi.step.review"), state: "todo" },
          { id: "enable", label: t("catalogUi.step.enable"), state: "todo" },
        ]}
      />
    );
  }
  const status = bundle.status ?? "pending";
  const waiting = status === "pending" && (bundle.origin !== "upload" || !read.has(bundle.name ?? ""));
  const review: StepState = waiting ? "current" : "done";
  const enable: StepState = status === "active" ? "done" : waiting ? "todo" : "current";
  return (
    <DetailSteps
      steps={[
        { id: "install", label: t("catalogUi.step.install"), state: "done", note: t("plugins.installed") },
        {
          id: "review",
          label: t("catalogUi.step.review"),
          state: review,
          note: waiting ? t("skills.upload.readFirst") : null,
        },
        {
          id: "enable",
          label: t("catalogUi.step.enable"),
          state: enable,
          action: <BundleSwitch bundle={bundle} unread={isUnread(bundle, read)} />,
        },
      ]}
    />
  );
}

/**
 * One skill's detail panel (study 04, §2.7): what it is, what reaches the agent, its licence, where
 * it comes from and at which commit, and whether it works here. One `CatalogDetail` whatever it
 * shows, keeping the last target while it animates out so it does not empty as it leaves.
 */
export function SkillDetail({
  target,
  item,
  onClose,
  read,
  onRead,
  onInstall,
  installingId,
}: {
  target: SkillTarget | null;
  item: CatalogItem | null;
  onClose: () => void;
  read: ReadonlySet<string>;
  onRead: (name: string) => void;
  onInstall: (item: CatalogItem) => void;
  installingId: string | null;
}) {
  const t = useT();
  const last = useRef<{ target: SkillTarget; item: CatalogItem } | null>(null);
  if (target && item) last.current = { target, item };
  const shown = last.current;
  if (!shown) return null;
  const { target: tg, item: it } = shown;

  const subtitle = [it.author ? t("catalogUi.by", { author: it.author }) : "", it.license?.spdx ?? ""]
    .filter(Boolean)
    .join(" · ");

  let body = null;
  let footer = null;
  switch (tg.kind) {
    case "learned":
      body = <LearnedBody stat={tg.stat} candidate={tg.candidate} />;
      footer = <LearnedActions stat={tg.stat} />;
      break;
    case "library":
      body = <LibraryBody card={tg.card} />;
      // A library card is text reviewed in this repository, so there is no download step; importing
      // puts it in the learned store, where card reading decides whether a run sees it.
      footer = tg.card.imported ? (
        <Badge tone="ok">{t("skills.imported")}</Badge>
      ) : (
        <Button size="sm" disabled={installingId === it.id} onClick={() => onInstall(it)}>
          {t("skills.import")}
        </Button>
      );
      break;
    case "entry":
      body = <EntryBody entry={tg.entry} bundle={tg.bundle} onRead={onRead} onRemoved={onClose} />;
      footer = (
        <BundleSteps
          // The catalogue can say a skill is installed before the bundle list has caught up.
          bundle={tg.bundle ?? (tg.entry.installed ? { name: tg.entry.name, status: tg.entry.installed, origin: "catalog" } : undefined)}
          read={read}
          item={it}
          onInstall={onInstall}
          installing={installingId === it.id}
        />
      );
      break;
    case "bundle":
      body = <BundleBody bundle={tg.bundle} onRead={onRead} onRemoved={onClose} />;
      footer = <BundleSteps bundle={tg.bundle} read={read} item={it} installing={false} />;
      break;
    case "pinned":
      body = <PinnedBody item={tg.item} catalog={tg.catalog} />;
      break;
  }

  return (
    <CatalogDetail open={target !== null} onClose={onClose} title={it.name} subtitle={subtitle || undefined} footer={footer}>
      {body}
    </CatalogDetail>
  );
}
