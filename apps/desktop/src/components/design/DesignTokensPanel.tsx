import { useId, useState } from "react";
import { Loader2, Plus } from "lucide-react";

import { FindingList } from "@/components/design/FindingList";
import { NumberField } from "@/components/design/InspectorFields";
import type { DesignSession } from "@/components/design/useDesignSession";
import { Button } from "@/components/ui/button";
import { checkDesign, type DesignCheck } from "@/lib/api";
import { LIMITS, isHexColour, safeColour, type DesignDocument, type DesignOperation, type DesignTokens } from "@/lib/design/model";
import { useNum, useT } from "@/lib/i18n";

const TOKEN_NAME = /^[A-Za-z0-9_.-]{1,100}$/;

/** A colour field that only commits a strict hex: anything else stays a draft and is never painted. */
function HexField({ name, value, onCommit }: { name: string; value: string; onCommit: (hex: string) => void }) {
  const t = useT();
  const id = useId();
  const [draft, setDraft] = useState(value);
  const valid = isHexColour(draft);
  return (
    <div className="flex items-center gap-2">
      <span aria-hidden="true" className="h-5 w-5 shrink-0 rounded-sm border border-border" style={safeColour(value) ? { backgroundColor: safeColour(value) } : undefined} />
      <label htmlFor={id} className="min-w-0 flex-1 truncate text-sm text-foreground">
        {name}
      </label>
      <input
        id={id}
        value={draft}
        aria-invalid={!valid}
        spellCheck={false}
        onChange={(e) => setDraft(e.target.value.trim())}
        onBlur={() => (valid && draft !== value ? onCommit(draft) : setDraft(value))}
        onKeyDown={(e) => {
          if (e.key === "Enter" && valid && draft !== value) onCommit(draft);
        }}
        title={valid ? undefined : t("design.tokens.hexHint")}
        className="field h-8 w-28 px-2 font-mono text-xs"
      />
    </div>
  );
}

/**
 * The design system as data, editable here: colours (strict hex only), spacing and radii. Every
 * change is one `tokens.replace`, so it saves and undoes like any canvas edit, and the server's
 * component recipes travel along untouched. "Check against tokens" asks the server which elements
 * stray from them, with a way to each one.
 */
export function DesignTokensPanel({
  designId,
  doc,
  session,
  edit,
  onJump,
}: {
  designId: string;
  doc: DesignDocument;
  session: DesignSession;
  /** Apply operations to the canvas as one undoable step; false when they were refused. */
  edit: (ops: DesignOperation[], label: string) => boolean;
  onJump: (ids: string[]) => void;
}) {
  const t = useT();
  const num = useNum();
  const nameId = useId();
  const hexId = useId();
  const [newName, setNewName] = useState("");
  const [newHex, setNewHex] = useState("");
  const [check, setCheck] = useState<DesignCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState(false);
  const tokens = doc.tokens;

  function replace(next: DesignTokens): void {
    edit([{ type: "tokens.replace", tokens: next }], "design.did.tokens");
  }

  async function runCheck(): Promise<void> {
    setChecking(true);
    setCheckError(false);
    try {
      await session.flush();
      setCheck(await checkDesign(designId));
    } catch {
      setCheckError(true);
    } finally {
      setChecking(false);
    }
  }

  const canAdd = TOKEN_NAME.test(newName) && isHexColour(newHex) && tokens.colors[newName] === undefined;

  return (
    <div className="flex flex-col">
      <section className="flex flex-col gap-2 border-b border-hairline p-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("design.tokens.colours")}</h3>
        {Object.entries(tokens.colors).map(([name, hex]) => (
          <HexField key={`${name}:${hex}`} name={name} value={hex} onCommit={(value) => replace({ ...tokens, colors: { ...tokens.colors, [name]: value } })} />
        ))}
        <div className="flex items-end gap-2">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <label htmlFor={nameId} className="text-xs text-muted-foreground">{t("design.tokens.newName")}</label>
            <input id={nameId} value={newName} onChange={(e) => setNewName(e.target.value.trim())} className="field h-8 px-2 text-sm" />
          </div>
          <div className="flex w-28 flex-col gap-1">
            <label htmlFor={hexId} className="text-xs text-muted-foreground">{t("design.tokens.newHex")}</label>
            <input id={hexId} value={newHex} spellCheck={false} onChange={(e) => setNewHex(e.target.value.trim())} className="field h-8 px-2 font-mono text-xs" />
          </div>
          <Button
            type="button"
            size="icon"
            variant="outline"
            aria-label={t("design.tokens.addColour")}
            disabled={!canAdd}
            onClick={() => {
              replace({ ...tokens, colors: { ...tokens.colors, [newName]: newHex } });
              setNewName("");
              setNewHex("");
            }}
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </section>
      <section className="flex flex-col gap-2 border-b border-hairline p-3">
        <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("design.tokens.typography")}</h3>
        {Object.keys(tokens.typography).length === 0 ? (
          <p className="text-sm text-muted-foreground">{t("design.inspector.noTokens")}</p>
        ) : (
          <ul className="flex flex-col gap-1 text-sm">
            {Object.entries(tokens.typography).map(([name, token]) => (
              <li key={name} className="flex justify-between gap-2">
                <span className="truncate text-foreground">{name}</span>
                <span className="shrink-0 text-muted-foreground">{`${token.family} ${num(token.size)} · ${num(token.weight)}`}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section className="grid grid-cols-2 gap-2 border-b border-hairline p-3">
        <h3 className="col-span-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("design.tokens.spacing")}</h3>
        {Object.entries(tokens.spacing).map(([name, value]) => (
          <NumberField key={name} label={name} value={value} min={0} max={LIMITS.dimension} onCommit={(n) => replace({ ...tokens, spacing: { ...tokens.spacing, [name]: n } })} />
        ))}
        {Object.entries(tokens.radii).map(([name, value]) => (
          <NumberField key={`r:${name}`} label={t("design.tokens.radius", { name })} value={value} min={0} max={LIMITS.dimension} onCommit={(n) => replace({ ...tokens, radii: { ...tokens.radii, [name]: n } })} />
        ))}
      </section>
      <section className="flex flex-col gap-2 p-3">
        <Button type="button" size="sm" variant="outline" disabled={checking} onClick={() => void runCheck()}>
          {checking ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
          {t("design.check.run")}
        </Button>
        {checkError ? <p role="alert" className="text-sm text-bad-foreground">{t("design.check.error")}</p> : null}
        {check && check.findings.length === 0 ? <p className="text-sm text-ok-foreground">{t("design.check.clean")}</p> : null}
        {check ? <FindingList findings={check.findings} onJump={onJump} /> : null}
      </section>
    </div>
  );
}
