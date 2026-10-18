import { useEffect, useId, useState } from "react";

import { colourTokens, safeColour, type DesignDocument } from "@/lib/design/model";
import { useT } from "@/lib/i18n";

/** A number field that commits on Enter or on leaving it, never on every keystroke. */
export function NumberField({
  label,
  value,
  min,
  max,
  onCommit,
}: {
  label: string;
  value: number;
  min?: number;
  max?: number;
  onCommit: (value: number) => void;
}) {
  const id = useId();
  const [draft, setDraft] = useState(String(Math.round(value)));
  useEffect(() => setDraft(String(Math.round(value))), [value]);

  function commit(): void {
    const n = Number(draft);
    if (!Number.isFinite(n) || draft.trim() === "") return setDraft(String(Math.round(value)));
    const clamped = Math.min(max ?? Infinity, Math.max(min ?? -Infinity, n));
    if (clamped !== value) onCommit(clamped);
    else setDraft(String(Math.round(value)));
  }

  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <input
        id={id}
        type="number"
        inputMode="decimal"
        value={draft}
        min={min}
        max={max}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
        className="field h-8 w-full px-2 text-sm tabular-nums"
      />
    </div>
  );
}

/**
 * Pick a colour from the document's tokens. The list is the document's own tokens and nothing
 * else — no free-form colour box here, so a value that is not a validated token cannot be entered
 * from the inspector. The swatch beside it shows the current value after validation.
 */
export function ColourTokenSelect({
  label,
  doc,
  value,
  match,
  onChange,
}: {
  label: string;
  doc: DesignDocument;
  /** The token name currently selected, or undefined for "none". */
  value: string | undefined;
  /** When the element stores a hex rather than a name, the hex — matched back to its token. */
  match?: string;
  onChange: (tokenName: string | undefined) => void;
}) {
  const t = useT();
  const id = useId();
  const tokens = colourTokens(doc);
  const current = value ?? tokens.find(([, hex]) => match !== undefined && hex.toLowerCase() === match.toLowerCase())?.[0];
  const swatch = safeColour(current !== undefined ? doc.tokens.colors[current] : match);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="h-5 w-5 shrink-0 rounded-sm border border-border"
          style={swatch ? { backgroundColor: swatch } : undefined}
        />
        <select
          id={id}
          value={current ?? ""}
          onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
          className="field h-8 min-w-0 flex-1 px-2 text-sm"
        >
          <option value="">{tokens.length ? t("design.inspector.noToken") : t("design.inspector.noTokens")}</option>
          {tokens.map(([name]) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

export function FontTokenSelect({
  label,
  doc,
  value,
  onChange,
}: {
  label: string;
  doc: DesignDocument;
  value: string | undefined;
  onChange: (tokenName: string | undefined) => void;
}) {
  const t = useT();
  const id = useId();
  const tokens = Object.entries(doc.tokens.typography);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <select
        id={id}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)}
        className="field h-8 min-w-0 px-2 text-sm"
      >
        <option value="">{tokens.length ? t("design.inspector.noToken") : t("design.inspector.noTokens")}</option>
        {tokens.map(([name, token]) => (
          <option key={name} value={name}>
            {`${name} · ${token.family} ${token.size}`}
          </option>
        ))}
      </select>
    </div>
  );
}
