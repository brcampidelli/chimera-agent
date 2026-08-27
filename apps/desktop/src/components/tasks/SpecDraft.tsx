import { useState } from "react";
import { Loader2, Plus, Trash2, Wand2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { draftSpec, writeSpec, type SpecRequirement } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** What a check actually does, said in words instead of in jargon.
 *
 *  `contains` is a regular expression; `defines` is a symbol name; `absent` is a regex that must
 *  not appear. Those three words are the difference between a spec somebody reviewed and a spec
 *  somebody scrolled past, and this screen exists for the person who does not know them.
 */
export function checkLabel(check: string, t: (k: string) => string): string {
  if (check === "contains") return t("tasks.checkContains");
  if (check === "defines") return t("tasks.checkDefines");
  if (check === "absent") return t("tasks.checkAbsent");
  return check;
}

/** Describe a project in plain language; review what it will be judged on; start it.
 *
 *  The orchestrator behind this is the most capable thing in the app, and until now its only door
 *  was a field asking for the path of a YAML file. Everyone who could not write that YAML was
 *  standing outside.
 *
 *  The review step is not a formality and is not skippable. The spec is the ACCEPTANCE AUTHORITY:
 *  it is the only thing that decides when the project is finished, so a requirement nobody
 *  understood is a project that stops on a condition nobody chose. Both halves of each requirement
 *  are shown — the sentence and the check — because a drafted line whose words do not describe its
 *  check is the failure this whole flow has to avoid, and reading them together is the only way to
 *  catch it.
 */
export function SpecDraft({
  workspace,
  onStarted,
}: {
  workspace: string | null;
  onStarted: (specPath: string) => void;
}) {
  const t = useT();
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState<"draft" | "start" | null>(null);
  const [note, setNote] = useState("");
  const [name, setName] = useState("");
  const [requirements, setRequirements] = useState<SpecRequirement[] | null>(null);
  const [refused, setRefused] = useState(0);

  async function draft() {
    setBusy("draft");
    setNote("");
    try {
      const d = await draftSpec(description.trim(), workspace);
      setNote(d.note);
      setName(d.name);
      setRefused(d.refused_commands);
      setRequirements(d.note ? null : d.requirements);
    } catch {
      setNote(t("tasks.draftUnreachable"));
      setRequirements(null);
    } finally {
      setBusy(null);
    }
  }

  async function createAndStart() {
    if (!requirements?.length) return;
    setBusy("start");
    setNote("");
    try {
      // The kept requirements, not the drafted ones. If this sent the draft back, deleting a line
      // above would change nothing and the review would be decoration.
      const { path } = await writeSpec({ name, requirements, workspace });
      onStarted(path);
      setDescription("");
      setRequirements(null);
    } catch {
      setNote(t("tasks.draftUnreachable"));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-2 px-3 pb-2">
      <div className="flex flex-wrap items-start gap-2">
        <textarea
          className="field min-h-14 min-w-48 flex-1 px-2 py-1.5 text-xs"
          placeholder={t("tasks.describePlaceholder")}
          aria-label={t("tasks.describeProject")}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        <Button
          size="sm"
          variant="outline"
          type="button"
          disabled={!description.trim() || busy !== null}
          onClick={() => void draft()}
        >
          {busy === "draft" ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Wand2 className="h-3.5 w-3.5" />
          )}{" "}
          {t("tasks.draftSpec")}
        </Button>
      </div>

      {note ? (
        <p className="text-xs text-muted-foreground" role="status">
          {note}
        </p>
      ) : null}

      {requirements ? (
        <div className="flex flex-col gap-2 rounded-chip border border-border bg-surface-2 p-2">
          <div>
            <p className="text-xs font-medium">{t("tasks.specReview")}</p>
            <p className="mt-0.5 text-xs text-muted-foreground">{t("tasks.specReviewHint")}</p>
          </div>

          <ul className="flex flex-col gap-1.5">
            {requirements.map((r) => (
              <li key={r.id} className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-xs">{r.text || r.id}</p>
                  {/* The check itself, never hidden behind the sentence. */}
                  <p className="mt-0.5 break-all font-mono text-xs text-muted-foreground">
                    {checkLabel(r.check, t)} <span className="text-foreground">{r.target}</span>
                  </p>
                </div>
                <button
                  type="button"
                  aria-label={t("tasks.dropRequirement", { id: r.id })}
                  className="mt-0.5 px-1 text-muted-foreground hover:text-bad"
                  onClick={() =>
                    setRequirements((prev) => (prev ?? []).filter((x) => x.id !== r.id))
                  }
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>

          {/* Reported, never quietly dropped: the spec now verifies less than the draft proposed,
              and the person relying on it should know by how much. */}
          {refused > 0 ? (
            <p className="text-xs text-muted-foreground">
              {t("tasks.refusedCommands", { count: refused })}
            </p>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              type="button"
              disabled={requirements.length === 0 || busy !== null}
              onClick={() => void createAndStart()}
            >
              {busy === "start" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Plus className="h-3.5 w-3.5" />
              )}{" "}
              {t("tasks.createAndStart")}
            </Button>
            <span className="text-xs text-muted-foreground">
              {requirements.length === 0
                ? t("tasks.specReviewEmpty")
                : t("tasks.specWillLandIn", { name: `${name}.spec.yaml` })}
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
