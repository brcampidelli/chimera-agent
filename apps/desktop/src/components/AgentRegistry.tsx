import { useEffect, useId, useState } from "react";
import { Loader2, Plus, Trash2, Wand2 } from "lucide-react";
import { deleteAgent, designAgent, getAgentRegistry, putAgent } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/panel";
import { useT } from "@/lib/i18n";
import type { AgentDef } from "@/lib/types";

/**
 * The agents you can send work to.
 *
 * `getAgentRegistry`, `putAgent` and `deleteAgent` shipped with the board that dispatches by lane
 * and had zero references in `src/components/` — the registry was reachable only from the CLI or a
 * hand-written `curl`. So the board's "who works this" box was a free text field guessing against a
 * list the app never showed, and the first news that an id was wrong arrived as "0 worked" after a
 * dispatch. That is the same class of defect the whole v0.42.0 series existed to close: the
 * mechanism is there, the surface is not.
 *
 * The lane stays free text on the board, deliberately — filing work against an agent you have not
 * created yet is a reasonable thing to do, and a dropdown would make "the agents I have right now"
 * the limit of what I can plan. What was missing is the other half: somewhere to see the list, and
 * a way to find out that a lane has no runner before a dispatch tells you.
 */
export function AgentRegistry({ embedded = false }: { embedded?: boolean }) {
  const t = useT();
  const [agents, setAgents] = useState<AgentDef[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentDef | null>(null);
  const [busy, setBusy] = useState(false);
  // Describing an agent instead of filling its form from an empty page. The proposal goes INTO the
  // form rather than beside it: reviewing a design and editing an agent are the same act, and a
  // second surface would be a second thing to keep in step.
  const [describing, setDescribing] = useState(false);
  const [description, setDescription] = useState("");
  const [designNote, setDesignNote] = useState("");
  // Whether the open draft came from a design. The warning about tools has to stand WHILE the
  // tools are on screen: it was attached to the description box, and closing that box on
  // success took the sentence away at the exact moment the generous tool list appeared.
  const [fromDesign, setFromDesign] = useState(false);

  const load = () => {
    getAgentRegistry()
      .then((list) => {
        setAgents(list);
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };

  useEffect(load, []);

  const save = async (agent: AgentDef) => {
    const id = agent.id.trim();
    if (!id) return;
    setBusy(true);
    try {
      setAgents(await putAgent({ ...agent, id }));
      setDraft(null);
      setFromDesign(false);
      setDesignNote("");
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const design = async () => {
    setBusy(true);
    setDesignNote("");
    try {
      const d = await designAgent(description.trim());
      setDesignNote(d.note);
      if (d.id || d.name) {
        setDraft({
          id: d.id,
          name: d.name,
          instructions: d.instructions,
          model: "",
          allowed_tools: d.allowed_tools,
        });
        setFromDesign(true);
        setDescribing(false);
        setDescription("");
      }
    } catch (e: unknown) {
      setDesignNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => {
    setBusy(true);
    try {
      setAgents(await deleteAgent(id));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={embedded ? "" : "p-4"} aria-label={t("registry.title")}>
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
        <p className="max-w-measure text-xs text-muted-foreground">{t("registry.blurb")}</p>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setDescribing((v) => !v)}
          disabled={busy || draft !== null}
        >
          <Wand2 className="h-3.5 w-3.5" /> {t("registry.describe")}
        </Button>
        <Button
          size="sm"
          onClick={() => setDraft({ id: "", name: "", instructions: "", model: "", allowed_tools: [] })}
          disabled={busy || draft !== null}
        >
          <Plus className="size-3.5" aria-hidden />
          {t("registry.add")}
        </Button>
      </div>

      {describing && (
        <div className="flex flex-col gap-2 px-3 pb-2">
          <div className="flex flex-wrap items-start gap-2">
            <textarea
              className="field min-h-14 min-w-48 flex-1 px-2 py-1.5 text-xs"
              placeholder={t("registry.describePlaceholder")}
              aria-label={t("registry.describe")}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              disabled={busy}
            />
            <Button size="sm" disabled={!description.trim() || busy} onClick={() => void design()}>
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Wand2 className="h-3.5 w-3.5" />
              )}{" "}
              {t("registry.designIt")}
            </Button>
          </div>
          {designNote && (
            <p className="text-xs text-warn-foreground" role="status">
              {designNote}
            </p>
          )}
        </div>
      )}

      {error && (
        <p role="alert" className="px-3 py-2 text-xs text-bad-foreground">
          {error}
        </p>
      )}

      {draft && fromDesign && (
        <div className="px-3 pb-1">
          {/* Standing where the tools are, not where the description was. A design arrives with
              tools already chosen and it chooses them generously: measured on three real
              descriptions, an agent asked only to say what is weak about a marketing text came back
              holding `edit_file`. */}
          <p className="text-xs text-muted-foreground">{t("registry.describeHint")}</p>
          {designNote && <p className="text-xs text-warn-foreground">{designNote}</p>}
        </div>
      )}
      {draft && (
        <AgentForm
          draft={draft}
          onChange={setDraft}
          onSave={save}
          onCancel={() => {
            setDraft(null);
            setFromDesign(false);
            setDesignNote("");
          }}
          busy={busy}
        />
      )}

      {agents === null && !error && (
        <p className="px-3 py-2 text-xs text-muted-foreground">{t("common.loading")}</p>
      )}

      {/* An empty registry is a normal state, not an error — dispatch falls back to the built-in
          runner. Saying so is what stops it reading as "something failed to load". */}
      {agents?.length === 0 && (
        <p className="px-3 py-6 text-center text-xs text-muted-foreground">{t("registry.empty")}</p>
      )}

      <ul className="grid gap-2 px-3 pb-3">
        {agents?.map((agent) => (
          <li key={agent.id} className="surface flex flex-wrap items-center gap-2 p-3">
            <code className="font-mono text-sm text-accent2">{agent.id}</code>
            {agent.name && <span className="text-sm">{agent.name}</span>}
            {agent.model && <Badge tone="muted">{agent.model}</Badge>}
            <ToolsBadge tools={agent.allowed_tools ?? []} />
            <div className="ml-auto flex items-center gap-1">
              <Button size="sm" variant="ghost" onClick={() => setDraft(agent)} disabled={busy}>
                {t("common.edit")}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                aria-label={t("registry.remove", { id: agent.id })}
                onClick={() => void remove(agent.id)}
                disabled={busy}
              >
                <Trash2 className="size-3.5" aria-hidden />
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Empty means NO RESTRICTION here, the same reading every other list in this project's
 *  configuration has — so an empty list must not render as "no tools". */
function ToolsBadge({ tools }: { tools: string[] }) {
  const t = useT();
  return (
    <Badge tone={tools.length === 0 ? "muted" : "accent"}>
      {tools.length === 0 ? t("registry.allTools") : t("registry.nTools", { n: tools.length })}
    </Badge>
  );
}

function AgentForm({
  draft,
  onChange,
  onSave,
  onCancel,
  busy,
}: {
  draft: AgentDef;
  onChange: (a: AgentDef) => void;
  onSave: (a: AgentDef) => void | Promise<void>;
  onCancel: () => void;
  busy: boolean;
}) {
  const t = useT();
  const formId = useId();
  return (
    <form
      className="surface mx-3 mb-3 grid gap-2 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        void onSave(draft);
      }}
    >
      <div className="grid gap-1 text-xs">
        <label className="grid gap-1" htmlFor={`${formId}-id`}>
          {t("registry.id")}
        </label>
        <input
          id={`${formId}-id`}
          aria-describedby={`${formId}-id-hint`}
          className="field h-8 px-2 font-mono text-sm"
          value={draft.id}
          onChange={(e) => onChange({ ...draft, id: e.target.value })}
          placeholder="reviewer"
          required
        />
        <span id={`${formId}-id-hint`} className="text-xs text-muted-foreground">
          {t("registry.idHint")}
        </span>
      </div>
      <label className="grid gap-1 text-xs">
        {t("registry.name")}
        <input
          className="field h-8 px-2 text-sm"
          value={draft.name}
          onChange={(e) => onChange({ ...draft, name: e.target.value })}
        />
      </label>
      <label className="grid gap-1 text-xs">
        {t("registry.model")}
        <input
          className="field h-8 px-2 font-mono text-sm"
          value={draft.model}
          onChange={(e) => onChange({ ...draft, model: e.target.value })}
          placeholder={t("registry.modelDefault")}
        />
      </label>
      <label className="grid gap-1 text-xs">
        {t("registry.instructions")}
        <textarea
          className="field min-h-16 px-2 py-1 text-sm"
          value={draft.instructions}
          onChange={(e) => onChange({ ...draft, instructions: e.target.value })}
        />
      </label>
      <label className="grid gap-1 text-xs">
        {t("registry.tools")}
        <input
          className="field h-8 px-2 font-mono text-sm"
          value={(draft.allowed_tools ?? []).join(", ")}
          onChange={(e) =>
            onChange({
              ...draft,
              allowed_tools: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="read_file, write_file"
        />
      </label>
      {/* Empty means no restriction here, the same reading every other list in this project's
          configuration has. Saying it beats letting someone discover it — and it sits outside the
          label so it describes the field instead of becoming part of its name. */}
      <span className="-mt-1 text-xs text-muted-foreground">{t("registry.toolsHint")}</span>
      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={busy || !draft.id.trim()}>
          {t("common.save")}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onCancel} disabled={busy}>
          {t("common.cancel")}
        </Button>
      </div>
    </form>
  );
}
