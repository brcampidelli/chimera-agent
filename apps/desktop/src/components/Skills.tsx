import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Sparkles } from "lucide-react";
import {
  approveSkill,
  getSkillLibrary,
  getSkillLibraryCard,
  getSkills,
  importSkillLibraryCard,
  retireSkill,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { Dialog } from "@/components/ui/dialog";
import { ErrorState } from "@/components/ui/async";
import { SkillCatalog } from "@/components/SkillCatalog";
import { useT } from "@/lib/i18n";
import type { LibraryCard, SkillStat } from "@/lib/types";

function statusTone(status: string): "ok" | "accent" | "warn" | "muted" {
  if (status === "active") return "ok";
  if (status === "provisional") return "accent";
  if (status === "pending") return "warn";
  return "muted";
}

/** Reading order through a piece of work, which is how the cards are grouped everywhere else the
 *  project shows them. Alphabetical would put "verify" before "build". */
const STAGES = ["define", "build", "verify", "review", "ship"] as const;

function byStage(cards: LibraryCard[]): [string, LibraryCard[]][] {
  const groups = new Map<string, LibraryCard[]>();
  for (const stage of STAGES) groups.set(stage, []);
  for (const card of cards) {
    // A card with no stage keeps its own group rather than being filed under a guess. The library's
    // own cards all declare one (a test fails the build otherwise); an imported third-party card
    // legitimately does not, and inventing a stage for it would be inventing advice about it.
    const key = STAGES.includes(card.stage as (typeof STAGES)[number]) ? card.stage : "";
    groups.set(key, [...(groups.get(key) ?? []), card]);
  }
  return [...groups.entries()].filter(([, list]) => list.length > 0);
}

/** The cards that ship in the box.
 *
 * The panel above this one shows skills the agent DISTILLED from the user's own verified runs — so
 * on a fresh install it is empty, which was every new user's entire impression of this screen. The
 * twenty-three curated cards existed the whole time, validated by their own test suite, reachable
 * only by a CLI command naming a path inside a git checkout.
 */
function Library() {
  const t = useT();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const library = useQuery({ queryKey: ["skill-library"], queryFn: getSkillLibrary });
  const card = useQuery({
    queryKey: ["skill-library", open],
    queryFn: () => getSkillLibraryCard(open as string),
    // The body is fetched only when a card is actually opened — the list deliberately carries
    // metadata alone, and prefetching every body would spend a quarter of a megabyte to draw names.
    enabled: open !== null,
  });
  const load = useMutation({
    mutationFn: importSkillLibraryCard,
    onSuccess: () => {
      // Both lists: the card moves into the learned store above AND its row here must stop offering
      // an import that has already happened.
      qc.invalidateQueries({ queryKey: ["skills"] });
      qc.invalidateQueries({ queryKey: ["skill-library"] });
    },
  });

  const cards = library.data ?? [];

  return (
    <>
      <Panel title={t("skills.library")}>
        {library.isError ? (
          <ErrorState error={library.error} onRetry={() => library.refetch()} />
        ) : library.isLoading ? (
          <Spinner />
        ) : cards.length === 0 ? (
          // Not "you have none yet": these are shipped, never earned, so an empty list means this
          // build lost them rather than that the user has not done anything.
          <EmptyState text={t("skills.libraryEmpty")} />
        ) : (
          byStage(cards).map(([stage, group]) => (
            <div key={stage || "none"}>
              <div className="bg-surface-2 px-4 py-1.5 text-xs font-semibold text-muted-foreground">
                {stage ? t(`skills.stage.${stage}`) : t("skills.stageNone")}
              </div>
              {group.map((c) => (
                <div key={c.name} className="flex items-center gap-3 px-4 py-3">
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => setOpen(c.name)}
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate font-mono text-sm">{c.name}</span>
                      {c.kind === "anti_pattern" && <Badge tone="warn">{c.kind}</Badge>}
                      {c.imported && <Badge tone="ok">{t("skills.imported")}</Badge>}
                    </div>
                    <div className="mt-0.5 text-xs text-muted-foreground">{c.description}</div>
                  </button>
                  <div className="flex shrink-0 gap-2">
                    {c.imported ? null : (
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={load.isPending}
                        onClick={() => load.mutate(c.name)}
                      >
                        {t("skills.import")}
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ))
        )}
      </Panel>

      <Dialog
        open={open !== null}
        onOpenChange={(next) => !next && setOpen(null)}
        title={open ?? ""}
        description={card.data?.description}
      >
        {card.isError ? (
          <ErrorState error={card.error} onRetry={() => card.refetch()} />
        ) : card.isLoading || !card.data ? (
          <Spinner />
        ) : (
          <>
            {card.data.triggers.length > 0 && (
              <p className="mb-3 text-xs text-muted-foreground">
                {t("skills.cardTriggers")}: {card.data.triggers.join(" · ")}
              </p>
            )}
            {/* The card's own markdown, shown as text rather than rendered. It is what the agent
                reads verbatim, and a renderer here would show the user a prettier document than the
                one the model gets — which is the wrong thing to be looking at when the question is
                why a card behaved as it did. */}
            <pre className="max-h-96 overflow-y-auto whitespace-pre-wrap break-words text-xs text-muted-foreground">
              {card.data.body}
            </pre>
          </>
        )}
      </Dialog>
    </>
  );
}

export function Skills({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const skills = useQuery({ queryKey: ["skills"], queryFn: getSkills });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["skills"] });
  const approve = useMutation({ mutationFn: approveSkill, onSuccess: invalidate });
  const retire = useMutation({ mutationFn: retireSkill, onSuccess: invalidate });

  const rows: SkillStat[] = skills.data?.stats ?? [];
  const candidates = new Set(skills.data?.retirement_candidates ?? []);

  return (
    <Screen title={t("skills.title")} icon={<Sparkles className="h-5 w-5" />} embedded={embedded}>
      <Panel title={t("skills.learned")}>
        {skills.isError ? (
          <ErrorState error={skills.error} onRetry={() => skills.refetch()} />
        ) : skills.isLoading ? (
          <Spinner />
        ) : rows.length === 0 ? (
          <EmptyState text={t("skills.empty")} />
        ) : (
          rows.map((s) => (
            <div key={s.name} className="flex items-center gap-3 px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate font-mono text-sm">{s.name}</span>
                  <Badge tone={statusTone(s.status)}>
                  {/* Translated, not echoed. The backend's value is an identifier; a badge is
                      read by a person, and an interface that ships in ten languages was showing
                      "active" and "retired" in all of them. Unknown states fall back to the raw
                      value rather than to an empty badge — a new state from a newer backend
                      should look odd, not invisible. */}
                  {t(`skills.status.${s.status}`) === `skills.status.${s.status}`
                    ? s.status
                    : t(`skills.status.${s.status}`)}
                </Badge>
                  {s.provenance === "tainted" && <Badge tone="warn">tainted</Badge>}
                  {candidates.has(s.name) && <Badge tone="bad">retire?</Badge>}
                </div>
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {t("skills.stats", { uses: s.uses, wins: s.successes })}
                  {s.rate != null && ` · ${Math.round(s.rate * 100)}%`}
                </div>
              </div>
              <div className="flex shrink-0 gap-2">
                {s.status === "pending" && (
                  <Button size="sm" onClick={() => approve.mutate(s.name)}>
                    {t("common.approve")}
                  </Button>
                )}
                {/* Retiring was a one-way door on this screen. The backend calls it "proposed for
                    retirement, kept for review", and `approve` is documented as the transition that
                    un-retires — but the only button that called it was gated on `pending`, so a
                    retired skill had no control at all beside it. Found on a real install: one card,
                    imported, retired, 0 uses, and no way back short of editing skills.json. Same
                    route, different word: this is not approving a stranger's card, it is putting
                    your own back to work. */}
                {s.status === "retired" && (
                  <Button size="sm" onClick={() => approve.mutate(s.name)}>
                    {t("skills.reactivate")}
                  </Button>
                )}
                {s.status !== "retired" && (
                  <Button size="sm" variant="outline" onClick={() => retire.mutate(s.name)}>
                    {t("skills.retire")}
                  </Button>
                )}
              </div>
            </div>
          ))
        )}
      </Panel>

      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <BookOpen className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {t("skills.libraryBlurb")}
      </p>
      <Library />
      <SkillCatalog />
    </Screen>
  );
}
