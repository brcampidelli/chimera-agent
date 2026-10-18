import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { CatalogEntry, LibraryCard, SkillBundle } from "@/lib/types";
import { LearnedBadges } from "@/components/skills/SkillParts";
import { otherFiles, ratingOf, STAGES, TONES } from "@/components/skills/skillItems";
import type { SkillTarget } from "@/components/skills/targets";

/** The verdict, before the button: whether the thing will run at all on this machine. "Works here"
 *  is drawn too — it is a judgement about a skill written for another agent, not a default. */
export function Portability({ entry }: { entry: CatalogEntry }) {
  const t = useT();
  const rating = ratingOf(entry);
  return <Badge tone={TONES[rating] ?? "muted"}>{t(`catalog.portability.${rating}`)}</Badge>;
}

/** The reason, in the reader's language. What arrives from the catalogue is the DETAIL — "macOS",
 *  "a GPU and the model weights" — and the sentence around it is translated here. A rating with no
 *  template falls back to the bare detail rather than to nothing. */
export function PortabilityReason({ entry }: { entry: CatalogEntry }) {
  const t = useT();
  const rating = ratingOf(entry);
  if (!entry.note) return null;
  const key = `catalog.reason.${rating}`;
  return <p className="text-xs text-warn-foreground">{t(key) === key ? entry.note : t(key, { detail: entry.note })}</p>;
}

export function Requires({ entry }: { entry: CatalogEntry }) {
  const t = useT();
  if (!entry.requires || entry.requires.length === 0) return null;
  return (
    <p className="text-xs text-muted-foreground">
      {t("catalog.needs")} <span className="font-mono">{entry.requires.join(", ")}</span>
    </p>
  );
}

function StageChip({ card }: { card: LibraryCard }) {
  const t = useT();
  const known = (STAGES as readonly string[]).includes(card.stage);
  // A card with no stage says so rather than being filed under a guess: an imported third-party
  // card legitimately has none, and inventing one would be inventing advice about it.
  return <Badge tone="muted">{known ? t(`skills.stage.${card.stage}`) : t("skills.stageNone")}</Badge>;
}

function FilesNote({ bundle }: { bundle: SkillBundle }) {
  const t = useT();
  const n = otherFiles(bundle);
  return n > 0 ? <p className="text-xs text-muted-foreground">{t("skills.card.scripts", { n })}</p> : null;
}

/**
 * The line a skill's card needs under the shared meta, by source. Kept to what decides whether to
 * open it: the verdict and what it needs for a catalogue entry; the stage and triggers for a library
 * card; the state and the two warnings for a learned one. Everything else is in the detail panel.
 */
export function SkillCardNote({ target, error }: { target: SkillTarget; error?: string | null }) {
  const t = useT();
  const failed = error ? <p className="text-xs text-bad-foreground">{error}</p> : null;
  switch (target.kind) {
    case "learned":
      return <LearnedBadges stat={target.stat} candidate={target.candidate} />;
    case "library": {
      const { card } = target;
      return (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <StageChip card={card} />
            {card.kind === "anti_pattern" && <Badge tone="warn">{card.kind}</Badge>}
            {card.imported && <Badge tone="ok">{t("skills.imported")}</Badge>}
          </div>
          {/* The triggers on the card, not only inside the panel: they decide whether a card is the
              one for the job, and opening every panel was the only way to compare them. */}
          {card.triggers.length > 0 ? (
            <p className="text-xs text-muted-foreground">
              {t("skills.cardTriggers")}: {card.triggers.join(" · ")}
            </p>
          ) : null}
          {failed}
        </div>
      );
    }
    case "entry": {
      const { entry, bundle } = target;
      return (
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Portability entry={entry} />
          </div>
          <PortabilityReason entry={entry} />
          <Requires entry={entry} />
          {bundle?.reconfirm ? <p className="text-xs text-warn-foreground">{t("catalog.reconfirm")}</p> : null}
          {failed}
        </div>
      );
    }
    case "bundle": {
      const { bundle } = target;
      return (
        <div className="space-y-1">
          {/* Every time, not only while pending: switching it on is consent to its instructions,
              not a change in where they came from. */}
          {bundle.origin === "upload" ? (
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="warn">{t("skills.upload.untrusted")}</Badge>
            </div>
          ) : null}
          <FilesNote bundle={bundle} />
          {bundle.reconfirm ? <p className="text-xs text-warn-foreground">{t("catalog.reconfirm")}</p> : null}
          {failed}
        </div>
      );
    }
    case "pinned":
      return failed;
  }
}
