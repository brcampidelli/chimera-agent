import { useEffect, useId, useState, type ReactNode } from "react";
import type { HtmlElementSelection } from "@/components/design/HtmlArtboardFrame";
import { Check, Trash2 } from "lucide-react";

import { ColourTokenSelect, FontTokenSelect, NumberField } from "@/components/design/InspectorFields";
import type { Dispatch } from "@/components/design/useCanvasPointer";
import { focusRing } from "@/components/ui/focus";
import { describeElement } from "@/lib/design/htmlElements";
import { extractHtmlTweaks } from "@/lib/design/htmlArtboard";
import { LIMITS, locateNode, newId, placeOf, type Artboard, type DesignDocument, type DesignNode, type DesignPage, type Rect } from "@/lib/design/model";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-b border-hairline p-3">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{title}</h3>
      {children}
    </section>
  );
}

function HtmlTweaksSection({ board, pageId, dispatch }: { board: Artboard; pageId: string; dispatch: Dispatch }) {
  const t = useT();
  const tweaks = extractHtmlTweaks(board.html ?? "");
  const [error, setError] = useState(false);
  if (tweaks.length === 0) return null;
  const setValue = (name: string, value: string) => {
    const ok = dispatch([{ type: "artboard.set_tweak", pageId, artboardId: board.id, name, value }], { label: "design.did.style" });
    setError(!ok);
  };
  return <Section title={t("design.html.tweaks")}>
    {tweaks.map((tweak) => <label key={tweak.name} className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">
      {tweak.label}
      {tweak.kind === "color" ? <input aria-label={tweak.label} type="color" value={tweak.value.slice(0, 7)} onChange={(event) => setValue(tweak.name, event.currentTarget.value)} className="field h-9 w-full px-1" />
        : tweak.kind === "number" ? <input aria-label={tweak.label} type="number" min={tweak.minimum} max={tweak.maximum} step={tweak.step} value={tweak.value} onChange={(event) => setValue(tweak.name, event.currentTarget.value)} className="field px-2 py-1 text-sm" />
          : <select aria-label={tweak.label} value={tweak.value} onChange={(event) => setValue(tweak.name, event.currentTarget.value)} className="field px-2 py-1 text-sm">{tweak.options.map((option) => <option key={option} value={option}>{option}</option>)}</select>}
    </label>)}
    {error && <p role="alert" className="text-xs text-destructive-ink">{t("design.html.tweakError")}</p>}
  </Section>;
}

function FrameFields({ frame, onFrame }: { frame: Rect; onFrame: (frame: Rect) => void }) {
  const t = useT();
  return (
    <div className="grid grid-cols-2 gap-2">
      <NumberField label={t("design.inspector.x")} value={frame.x} onCommit={(x) => onFrame({ ...frame, x })} />
      <NumberField label={t("design.inspector.y")} value={frame.y} onCommit={(y) => onFrame({ ...frame, y })} />
      <NumberField label={t("design.inspector.width")} value={frame.width} min={1} max={LIMITS.dimension} onCommit={(width) => onFrame({ ...frame, width })} />
      <NumberField label={t("design.inspector.height")} value={frame.height} min={1} max={LIMITS.dimension} onCommit={(height) => onFrame({ ...frame, height })} />
    </div>
  );
}

function TextField({ value, onCommit }: { value: string; onCommit: (value: string) => void }) {
  const t = useT();
  const id = useId();
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {t("design.inspector.text")}
      </label>
      <textarea
        id={id}
        value={draft}
        rows={3}
        maxLength={LIMITS.text}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        className="field resize-y px-2 py-1 text-sm"
      />
    </div>
  );
}

function OpacityField({ value, onCommit }: { value: number; onCommit: (value: number) => void }) {
  const t = useT();
  const id = useId();
  const percent = Math.round(value * 100);
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {t("design.inspector.opacity", { percent })}
      </label>
      <input
        id={id}
        type="range"
        min={0}
        max={100}
        step={5}
        value={percent}
        onChange={(e) => onCommit(Number(e.target.value) / 100)}
        className="w-full accent-[hsl(var(--accent))]"
      />
    </div>
  );
}

const TYPE_LABEL: Record<DesignNode["type"], string> = {
  text: "design.type.text",
  shape: "design.type.shape",
  image: "design.type.image",
  group: "design.type.group",
};

function NodeSection({ doc, node, dispatch }: { doc: DesignDocument; node: DesignNode; dispatch: Dispatch }) {
  const t = useT();
  const update = (patch: Record<string, unknown>, label: string, coalesceKey?: string) =>
    dispatch([{ type: "node.update", nodeId: node.id, patch }], { label, coalesceKey });
  return (
    <Section title={node.name || t(TYPE_LABEL[node.type])}>
      {node.locked && <p className="text-xs text-muted-foreground">{t("design.inspector.locked")}</p>}
      <FrameFields frame={node.frame} onFrame={(frame) => dispatch([{ type: "node.move", nodeId: node.id, frame }], { label: "design.did.move" })} />
      {node.type === "text" && (
        <>
          <TextField value={node.text} onCommit={(text) => update({ text }, "design.did.editText")} />
          <FontTokenSelect label={t("design.inspector.font")} doc={doc} value={node.fontToken} onChange={(fontToken) => update({ fontToken: fontToken ?? null }, "design.did.style")} />
          <ColourTokenSelect label={t("design.inspector.colour")} doc={doc} value={node.colorToken} onChange={(colorToken) => update({ colorToken: colorToken ?? null }, "design.did.style")} />
        </>
      )}
      {node.type === "shape" && (
        <ColourTokenSelect
          label={node.shape === "line" || node.shape === "path" ? t("design.inspector.stroke") : t("design.inspector.fill")}
          doc={doc}
          value={undefined}
          match={node.shape === "line" || node.shape === "path" ? node.style?.stroke : node.style?.fill}
          onChange={(name) => {
            const key = node.shape === "line" || node.shape === "path" ? "stroke" : "fill";
            const style = { ...node.style };
            if (name === undefined) delete style[key];
            else style[key] = doc.tokens.colors[name];
            update({ style }, "design.did.style");
          }}
        />
      )}
      <OpacityField
        value={node.style?.opacity ?? 1}
        onCommit={(opacity) => update({ style: { ...node.style, opacity } }, "design.did.style", `opacity:${node.id}`)}
      />
    </Section>
  );
}

function ArtboardSection({ doc, page, board, dispatch }: { doc: DesignDocument; page: DesignPage; board: Artboard; dispatch: Dispatch }) {
  const t = useT();
  const patch = (p: Partial<Artboard>) =>
    dispatch([{ type: "artboard.update", pageId: page.id, artboardId: board.id, patch: p }], { label: "design.did.artboard" });
  return (
    <Section title={t("design.inspector.artboard", { name: board.name })}>
      <div className="grid grid-cols-2 gap-2">
        <NumberField label={t("design.inspector.width")} value={board.width} min={1} max={LIMITS.dimension} onCommit={(width) => patch({ width })} />
        <NumberField label={t("design.inspector.height")} value={board.height} min={1} max={LIMITS.dimension} onCommit={(height) => patch({ height })} />
      </div>
      {board.html != null ? <>
        <p className="text-xs text-muted-foreground">{t("design.inspector.htmlMode", { mode: board.mode ?? "fixed" })}</p>
        <p className="text-xs text-muted-foreground">{t("design.inspector.htmlNotice")}</p>
      </> : <ColourTokenSelect
        label={t("design.inspector.background")}
        doc={doc}
        value={undefined}
        match={board.background}
        onChange={(name) => name !== undefined && patch({ background: doc.tokens.colors[name] })}
      />}
    </Section>
  );
}

/** A comment on one element, pinned to its top-left corner. */
function AddComment({ doc, node, author, dispatch }: { doc: DesignDocument; node: DesignNode; author: string; dispatch: Dispatch }) {
  const t = useT();
  const id = useId();
  const [text, setText] = useState("");
  const where = placeOf(doc, node.id);
  if (!where) return null;
  function add(): void {
    if (!where || text.trim() === "") return;
    const comment = {
      id: newId("comment"),
      artboardId: where.artboard.id,
      nodeId: node.id,
      x: node.frame.x,
      y: node.frame.y,
      text: text.trim(),
      author,
      resolved: false,
      createdAt: new Date().toISOString(),
    };
    if (dispatch([{ type: "comment.add", comment }], { label: "design.did.comment" })) setText("");
  }
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {t("design.comment.placeholder", { name: node.name })}
      </label>
      <textarea
        id={id}
        value={text}
        rows={2}
        maxLength={LIMITS.comment}
        onChange={(e) => setText(e.target.value)}
        className="field resize-y px-2 py-1 text-sm"
      />
      <button
        type="button"
        disabled={text.trim() === ""}
        onClick={add}
        className={cn("self-start rounded-lg px-2 py-1 text-sm text-accent-ink transition-colors duration-1 ease-out hover:bg-surface-hover disabled:opacity-50", focusRing)}
      >
        {t("design.comment.add")}
      </button>
    </div>
  );
}

/**
 * The page's comments, or one element's when one is selected, with a field to add to them: a
 * comment is about an element, so that is where it is written.
 */
function CommentsSection({
  doc,
  page,
  node,
  author,
  dispatch,
}: {
  doc: DesignDocument;
  page: DesignPage;
  node?: DesignNode;
  author: string;
  dispatch: Dispatch;
}) {
  const t = useT();
  const boards = new Set(page.artboards.map((b) => b.id));
  const comments = (doc.comments ?? []).filter((c) => boards.has(c.artboardId) && (node === undefined || c.nodeId === node.id));
  if (comments.length === 0 && node === undefined) return null;
  const iconButton = cn("inline-flex h-7 w-7 items-center justify-center rounded-lg transition-colors duration-1 ease-out hover:bg-surface-hover", focusRing);
  return (
    <Section title={t("design.inspector.comments", { n: comments.length })}>
      <ul className="flex flex-col gap-2">
        {comments.map((c) => (
          <li key={c.id} className={cn("flex items-start gap-2 text-sm", c.resolved && "text-muted-foreground line-through")}>
            <span className="min-w-0 flex-1 break-words">
              <span className="font-medium">{c.author}</span> {c.text}
            </span>
            <button
              type="button"
              aria-label={t(c.resolved ? "design.comment.reopen" : "design.comment.resolve")}
              aria-pressed={c.resolved}
              onClick={() => dispatch([{ type: "comment.resolve", commentId: c.id, resolved: !c.resolved }], { label: "design.did.comment" })}
              className={iconButton}
            >
              <Check className="h-4 w-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-label={t("design.comment.delete")}
              onClick={() => dispatch([{ type: "comment.delete", commentId: c.id }], { label: "design.did.comment" })}
              className={iconButton}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      {node ? <AddComment doc={doc} node={node} author={author} dispatch={dispatch} /> : null}
    </Section>
  );
}

/**
 * The properties of what is selected: the element when there is one, its artboard otherwise, and
 * the page's comments. Every field commits as one operation batch through `dispatch`, so an edit
 * here is one step to undo, exactly like the same edit made on the canvas.
 */
const HTML_STYLE_PROPERTIES = ["color", "background", "font-size", "font-weight", "padding", "margin", "gap", "border-radius", "width", "height", "display"] as const;

function HtmlElementSection({ selection, doc, pageId, dispatch, onClear }: {
  selection: HtmlElementSelection & { artboardId: string };
  doc: DesignDocument;
  pageId: string;
  dispatch: Dispatch;
  onClear: () => void;
}) {
  const t = useT();
  const [error, setError] = useState("");
  const board = doc.pages.flatMap((item) => item.artboards).find((candidate) => candidate.id === selection.artboardId);
  // The document is the truth: after an edit the element's style and text are read again from the
  // source, so the fields show what the page now says, not what the last click reported.
  const current = board?.html != null ? describeElement(board.html, selection.index) : null;
  const gone = current === null;
  useEffect(() => {
    if (gone) onClear();
  }, [gone, onClear]);
  if (!current) return null;
  const declarations = Object.fromEntries(current.inlineStyle.split(";").flatMap((part) => {
    const colon = part.indexOf(":");
    return colon < 0 ? [] : [[part.slice(0, colon).trim().toLowerCase(), part.slice(colon + 1).trim()]];
  }));
  function setStyle(property: string, value: string) {
    const ok = dispatch([{ type: "artboard.set_style", pageId, artboardId: selection.artboardId, index: selection.index, property, value }], { label: "design.did.style" });
    setError(ok ? "" : t("design.html.error"));
  }
  function setText(value: string) {
    const ok = dispatch([{ type: "artboard.set_text", pageId, artboardId: selection.artboardId, index: selection.index, text: value }], { label: "design.did.editText" });
    setError(ok ? "" : t("design.html.error"));
  }
  return <Section title={`${t("design.html.element")} · ${current.tag}`}>
    <button type="button" onClick={onClear} className={cn("self-start rounded px-2 py-1 text-xs hover:bg-surface-hover", focusRing)}>{t("design.html.clear")}</button>
    <div className="grid grid-cols-2 gap-2">{HTML_STYLE_PROPERTIES.map((property) => <label key={property} className="flex min-w-0 flex-col gap-1 text-xs text-muted-foreground">{property}<input key={`${property}:${declarations[property] ?? ""}`} className="field min-w-0 px-2 py-1 text-sm" defaultValue={declarations[property] ?? ""} onBlur={(event) => { if (event.currentTarget.value !== (declarations[property] ?? "")) setStyle(property, event.currentTarget.value); }} /></label>)}</div>
    {current.text !== null && <label className="flex flex-col gap-1 text-xs text-muted-foreground">{t("design.inspector.text")}<textarea key={current.text} className="field px-2 py-1 text-sm" defaultValue={current.text} maxLength={4096} onBlur={(event) => { if (event.currentTarget.value !== current.text) setText(event.currentTarget.value); }} /></label>}
    {error && <p role="alert" className="text-xs text-destructive-ink">{error}</p>}
  </Section>;
}

export function DesignInspector({
  doc,
  pageId,
  selection,
  dispatch,
  author,
  htmlSelection,
  onHtmlSelectionChange,
  htmlNotice = false,
  activeArtboardId,
}: {
  doc: DesignDocument;
  pageId: string | undefined;
  selection: readonly string[];
  dispatch: Dispatch;
  /** Name stamped on a comment added here. */
  author?: string;
  htmlSelection?: (HtmlElementSelection & { artboardId: string }) | null;
  onHtmlSelectionChange?: (selection: (HtmlElementSelection & { artboardId: string }) | null) => void;
  /** The last click on an HTML element was discarded (too large or malformed): say so, do not go silent. */
  htmlNotice?: boolean;
  /** The board the reader is working on (picked in the list, by its label or by a click). */
  activeArtboardId?: string;
}) {
  const t = useT();
  const page = doc.pages.find((p) => p.id === pageId);
  if (!page) return null;
  const nodes = selection.map((id) => doc.nodes[id]).filter((n): n is DesignNode => n !== undefined);
  const where = nodes.length ? locateNode(doc, nodes[0].id) : undefined;
  const activeId = htmlSelection?.artboardId ?? activeArtboardId;
  const board = where?.artboard ?? page.artboards.find((candidate) => candidate.id === activeId) ?? page.artboards[0];
  return (
    <div aria-label={t("design.inspector.label")} role="region" className="flex flex-col">
      {htmlSelection && <HtmlElementSection selection={htmlSelection} doc={doc} pageId={doc.pages.find((item) => item.artboards.some((candidate) => candidate.id === htmlSelection.artboardId))?.id ?? page.id} dispatch={dispatch} onClear={() => onHtmlSelectionChange?.(null)} />}
      {htmlNotice && !htmlSelection && <p role="status" className="px-3 py-2 text-xs text-muted-foreground">{t("design.html.selectionTooLarge")}</p>}
      {nodes.length === 1 && <NodeSection key={nodes[0].id} doc={doc} node={nodes[0]} dispatch={dispatch} />}
      {nodes.length > 1 && (
        <Section title={t("design.announce.selectedMany", { n: nodes.length })}>
          <p className="text-sm text-muted-foreground">{t("design.inspector.multi")}</p>
        </Section>
      )}
      {nodes.length === 0 && board && <ArtboardSection doc={doc} page={page} board={board} dispatch={dispatch} />}
      {nodes.length === 0 && board?.html !== undefined && board.html !== null && <HtmlTweaksSection board={board} pageId={page.id} dispatch={dispatch} />}
      <CommentsSection
        doc={doc}
        page={page}
        node={nodes.length === 1 ? nodes[0] : undefined}
        author={author ?? t("design.comment.you")}
        dispatch={dispatch}
      />
    </div>
  );
}
