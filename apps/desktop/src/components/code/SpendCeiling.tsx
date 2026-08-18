import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { getDoctor } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * A dollar ceiling for this turn, and the honest answer about whether one can work here.
 *
 * The mechanism is old: the loop refuses the call that would cross the ceiling BEFORE making it and
 * keeps whatever it already has. What was missing was any way to name a number — the only caller in
 * the codebase was the cron dispatcher, so the surface where a person actually watches money being
 * spent could not set one.
 *
 * **The warning is the other half of the feature.** A cap stops the run when it meets a call it
 * cannot price, which is the only rule that does not lie — and it turns an unpriced default model
 * into a control that halts the first call and looks broken. `doctor` has measured that on THIS
 * machine all along (`pricing_capability`, which even names the model and how to fix it) and no
 * component read it. Shown while the ceiling is ARMED rather than always: warning about a limiter
 * nobody switched on is noise, and one keystroke later it is still before the Send.
 *
 * Rendered as a fragment so it can sit inside the composer's `flex-wrap` row; each note takes
 * `w-full` and therefore its own line, because it is a sentence, not a chip.
 */
export function SpendCeiling({
  onChange,
  disabled,
}: {
  /** The armed ceiling in dollars, or null when nothing usable is typed. */
  onChange: (v: number | null) => void;
  disabled?: boolean;
}) {
  const t = useT();
  // What was TYPED is the only state, and every claim below is derived from it. The first version
  // took the armed number back as a prop and asked whether the two agreed — which made the
  // component's warnings depend on the parent echoing it, so a parent that recorded the value
  // without re-rendering left the box permanently accusing itself of being empty. One source of
  // truth, and a controlled `value` would also erase the character as you type it: "0" arms
  // nothing, the input would re-render empty, and the box would fight the keyboard.
  const [text, setText] = useState("");
  const armed = parse(text);
  // Typed something, armed nothing. Said out loud rather than swallowed: someone who types $0
  // believes they capped this turn at nothing, and the server reads this field for truthiness — so
  // a silently-dropped zero would mean "spend anything" to the one person sure it meant the
  // opposite. The server refuses it with a 422 for the same reason.
  const unusable = text.trim() !== "" && armed === null;

  // Cached under the same key every other reader uses, so arming a ceiling costs no extra request.
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor });
  const spend = doctor.data?.spend;
  // `available: false` is a measurement, not an absence: `probed` is always true for this
  // capability (the price table either resolves the default model or it does not). An absent
  // `spend` means an older server, and inventing a verdict for it would be the one thing this
  // capability block exists to avoid.
  const unpriced = spend != null && !spend.available;

  return (
    <>
      <label
        className="flex items-center gap-1 text-xs text-muted-foreground"
        title={t("composer.spendCap.hint")}
      >
        {t("composer.spendCap")}
        <span aria-hidden>$</span>
        <input
          type="number"
          min={0}
          step={0.05}
          inputMode="decimal"
          className="field h-8 w-20 px-2 text-xs"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            onChange(parse(e.target.value));
          }}
          disabled={disabled}
        />
      </label>
      {unusable ? (
        <p className="flex w-full items-start gap-1.5 text-xs text-warn-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {t("composer.spendCap.positive")}
        </p>
      ) : null}
      {armed !== null && unpriced ? (
        <p className="flex w-full items-start gap-1.5 text-xs text-warn-foreground">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {/* The server's own sentence, interpolated rather than paraphrased: it names the model
              this machine would run and the one line that fixes it, and neither is knowable here.
              The frame around it is translated, so the clause that matters is not the only clause
              left in English. Same shape as the provider picker's `missing` hint. */}
          {t("composer.spendCap.unpriced", { hint: spend.hint })}
        </p>
      ) : null}
    </>
  );
}

/** The typed text as a ceiling, or null for "no ceiling" — which is what anything non-positive is. */
function parse(text: string): number | null {
  const n = Number(text);
  return text.trim() === "" || !Number.isFinite(n) || n <= 0 ? null : n;
}
