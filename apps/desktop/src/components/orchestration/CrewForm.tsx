import { useEffect, useState } from "react";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/lib/i18n";
import { getApproaches, type CrewApproach, type CrewWorkerInput } from "@/lib/api";

/**
 * Assembling a crew: who tries, and what decides which attempt lands.
 *
 * The check is the first field rather than the last, and that is not a layout preference. Every
 * worker attacks the SAME task, so they tend to edit the same files — and the merge rule is
 * one-file-one-owner, meaning that when two of them succeed on the same file, NEITHER lands.
 * Which inverts the intuition twice over: a crew with no check usually produces nothing, and a
 * check that every worker passes produces nothing either. What a crew needs is a check that
 * DISCRIMINATES, and workers different enough for it to have something to discriminate between.
 *
 * That is why the roles are picked from a catalogue instead of typed. Two blank instruction boxes
 * invited two ways of saying the same thing, and two workers with the same idea write the same
 * diff, both pass, and both get discarded. Each entry in the catalogue changes something
 * structural about the diff — how many files it touches, whether it edits tests, whether it
 * reaches outside the standard library — which is the axis a check can actually separate.
 */
const CUSTOM = "custom";

interface Slot {
  /** The catalogue entry, or CUSTOM. Also the worker's name (the routing key on every frame). */
  approachId: string;
  /** Only used when approachId is CUSTOM — otherwise the id is the name. */
  customName: string;
  instruction: string;
}

function blankSlot(): Slot {
  return { approachId: CUSTOM, customName: "", instruction: "" };
}

function slotName(slot: Slot): string {
  return (slot.approachId === CUSTOM ? slot.customName : slot.approachId).trim();
}

export function CrewForm({
  onRun,
  running,
}: {
  onRun: (workers: CrewWorkerInput[], verify: string) => void;
  running: boolean;
}) {
  const t = useT();
  const [verify, setVerify] = useState("");
  const [catalogue, setCatalogue] = useState<CrewApproach[]>([]);
  const [slots, setSlots] = useState<Slot[]>([blankSlot(), blankSlot()]);

  useEffect(() => {
    let live = true;
    void getApproaches()
      .then((result) => {
        if (!live) return;
        setCatalogue(result.approaches);
        // Seeded from the server's own default pair, not from a copy of it kept here. The two it
        // names are the widest apart in the catalogue, which is the pair most likely to leave
        // exactly one worker standing after a check.
        const byId = new Map(result.approaches.map((a) => [a.id, a]));
        const seeded = result.default
          .map((id) => byId.get(id))
          .filter((a): a is CrewApproach => a !== undefined)
          .map((a) => ({ approachId: a.id, customName: "", instruction: a.instruction }));
        if (seeded.length >= 2) setSlots(seeded);
      })
      // A catalogue that did not load leaves the two blank slots above, which still run. The
      // screen degrades to what it was rather than to nothing.
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const named = slots.filter((s) => slotName(s) && s.instruction.trim());
  const names = named.map(slotName);
  const duplicated = new Set(names).size !== names.length;
  // A catalogue worker is named by its approach, so this always implies `duplicated` — it is not
  // a second condition to refuse on, it is a better ACCOUNT of the same one.
  const chosen = slots.map((s) => s.approachId).filter((id) => id !== CUSTOM);
  const sameApproach = new Set(chosen).size !== chosen.length;

  function edit(index: number, patch: Partial<Slot>) {
    setSlots((prev) => prev.map((s, i) => (i === index ? { ...s, ...patch } : s)));
  }

  function choose(index: number, approachId: string) {
    const found = catalogue.find((a) => a.id === approachId);
    // Picking replaces the instruction, including one that was edited by hand: the alternative is
    // a box whose text no longer matches the role above it.
    edit(index, { approachId, instruction: found ? found.instruction : "" });
  }

  return (
    <div className="space-y-4 rounded-card border border-hairline bg-surface-2/40 p-4">
      <div>
        <label
          className="text-xs font-semibold uppercase tracking-wider text-muted-foreground"
          htmlFor="crew-verify"
        >
          {t("crew.verify.label")}
        </label>
        <input
          id="crew-verify"
          value={verify}
          onChange={(event) => setVerify(event.target.value)}
          placeholder={t("crew.verify.placeholder")}
          className="mt-1 w-full rounded-card border border-hairline bg-surface-2/40 p-2 font-mono text-xs text-foreground placeholder:text-muted-foreground"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          {verify.trim() ? t("crew.verify.why") : t("crew.verify.missing")}
        </p>
      </div>

      <div className="space-y-3">
        <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {t("crew.workers.label")}
        </span>

        {slots.map((slot, index) => (
          <div key={index} className="space-y-1.5 rounded-card border border-hairline p-2">
            <div className="flex items-center gap-2">
              <select
                value={slot.approachId}
                onChange={(event) => choose(index, event.target.value)}
                aria-label={t("crew.workers.approachOf", { n: index + 1 })}
                className="min-w-0 flex-1 rounded-card border border-hairline bg-surface-2/40 p-2 text-xs text-foreground"
              >
                {catalogue.map((approach) => (
                  <option key={approach.id} value={approach.id}>
                    {t(`crew.approach.${approach.id}`)}
                  </option>
                ))}
                <option value={CUSTOM}>{t("crew.approach.custom")}</option>
              </select>
              {slot.approachId === CUSTOM ? (
                <input
                  value={slot.customName}
                  onChange={(event) => edit(index, { customName: event.target.value })}
                  placeholder={t("crew.workers.name")}
                  aria-label={t("crew.workers.nameOf", { n: index + 1 })}
                  className="w-32 shrink-0 rounded-card border border-hairline bg-surface-2/40 p-2 text-xs text-foreground placeholder:text-muted-foreground"
                />
              ) : null}
              {slots.length > 1 ? (
                <button
                  type="button"
                  aria-label={t("crew.workers.remove", { n: index + 1 })}
                  onClick={() => setSlots((prev) => prev.filter((_, i) => i !== index))}
                  className="shrink-0 text-muted-foreground hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              ) : null}
            </div>

            {/* The exact prompt, one click away and editable. Folded rather than hidden: a
                summary of an instruction can drift from the instruction, and then the screen is
                telling you something other than what it sends. A picked role opens closed
                because its name already says what it does; a custom one has nothing else. */}
            <details open={slot.approachId === CUSTOM}>
              <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                {t("crew.workers.exactInstruction")}
              </summary>
              <textarea
                value={slot.instruction}
                onChange={(event) => edit(index, { instruction: event.target.value })}
                placeholder={t("crew.workers.instruction")}
                aria-label={t("crew.workers.instructionOf", { n: index + 1 })}
                rows={3}
                className="mt-1 w-full rounded-card border border-hairline bg-surface-2/40 p-2 text-xs text-foreground placeholder:text-muted-foreground"
              />
            </details>
          </div>
        ))}

        {slots.length < 8 ? (
          <Button size="sm" variant="ghost" onClick={() => setSlots((prev) => [...prev, blankSlot()])}>
            <Plus className="h-4 w-4" /> {t("crew.workers.add")}
          </Button>
        ) : null}
      </div>

      {/* Both are the same refusal — the server will not take two workers with one name, because
          the name routes every frame. Which of the two sentences to show is about the CAUSE: a
          worker built from the catalogue is named by its approach, so picking one twice collides
          on the name as a side effect. Reporting that as "two workers share a name" would name
          the mechanism and hide the mistake, which is that two identical roles write the same
          diff, both pass, and the conflict rule then discards both. */}
      {sameApproach ? (
        <p className="text-xs text-bad-foreground">{t("crew.workers.sameApproach")}</p>
      ) : duplicated ? (
        <p className="text-xs text-bad-foreground">{t("crew.workers.duplicate")}</p>
      ) : null}

      <Button
        size="sm"
        disabled={running || named.length < 2 || duplicated}
        onClick={() =>
          onRun(
            named.map((s) => ({ name: slotName(s), instruction: s.instruction.trim() })),
            verify.trim(),
          )
        }
      >
        {t("crew.run")}
      </Button>
      {named.length < 2 ? <p className="text-xs text-muted-foreground">{t("crew.needTwo")}</p> : null}
    </div>
  );
}
