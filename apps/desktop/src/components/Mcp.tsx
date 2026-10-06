import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plug,
  Trash2,
  Check,
  X,
  Loader2,
  Plus,
  ExternalLink,
  TriangleAlert,
  Search,
  History,
} from "lucide-react";
import {
  addMcpServer,
  approveMcpManifest,
  getConfig,
  getMcpCatalog,
  getMcpServers,
  removeMcpServer,
  testMcpServer,
} from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { useI18n, useT, type TFunc } from "@/lib/i18n";
import type { McpCatalogEntry } from "@/lib/api";
import type { McpServer, McpTest } from "@/lib/types";

/** What each cue code means, as dictionary keys written out so `i18n.reachable.test.ts` sees them.
 *  The codes come from `chimera/integrations/mcp_cues.py`; one the dictionary does not know is shown
 *  as the code itself rather than dropped, because a cue the owner cannot see is the failure. */
const CUE_TEXT: Record<string, string> = {
  imperative: "mcp.cue.imperative",
  exclusivity: "mcp.cue.exclusivity",
  override: "mcp.cue.override",
  emphasis: "mcp.cue.emphasis",
};

/** The selection-cue screen (study 30, S30-24): phrases in a description that try to steer which
 *  tool the model picks. An annotation beside the server's own text, never a refusal — a regular
 *  expression is the wrong instrument to refuse anything on, and nothing has measured how often it
 *  fires on honest servers. */
function CueLine({ cues, t }: { cues?: string[] | null; t: TFunc }) {
  if (!cues || cues.length === 0) return null;
  return (
    <span className="mt-0.5 flex items-start gap-1 text-xs text-warn-foreground">
      <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" />
      <span>
        {t("mcp.cue.title")} {cues.map((c) => (CUE_TEXT[c] ? t(CUE_TEXT[c]) : c)).join(", ")}
      </span>
    </span>
  );
}

const CHANGE_TEXT: Record<string, string> = {
  added: "mcp.held.added",
  removed: "mcp.held.removed",
  changed: "mcp.held.changed",
};

/** One side of a changed parameter schema, as the indented JSON the server sent. */
function SchemaText({ label, text, old = false }: { label: string; text: string; old?: boolean }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-muted-foreground">{label}</span>
      <pre
        className={`max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-chip bg-surface-2 p-1.5 font-mono text-xs ${
          old ? "text-muted-foreground" : "text-foreground"
        }`}
      >
        {text}
      </pre>
    </div>
  );
}

/** A server held from every run because its tools changed since the owner approved them.
 *
 *  The diff is the point: an Approve button without the old and new text beside it would be a
 *  rubber stamp on third-party text that goes straight into the model's tool list. That includes
 *  the parameter schema, shown whole: parameter descriptions are read by the model like the tool
 *  description, and "parameters changed" alone asked the owner to approve text nobody displayed. */
function HeldBlock({
  held,
  onApprove,
  approving,
  stale,
  failed,
  t,
}: {
  held: NonNullable<McpServer["manifest_held"]>;
  onApprove: () => void;
  approving: boolean;
  stale: boolean;
  failed: boolean;
  t: TFunc;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-chip border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn-foreground">
      <div className="flex items-start gap-1.5">
        <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>{t("mcp.held.title")}</span>
      </div>
      {/* Keyed by position: a server can list two tools with one name, and each is its own line. */}
      {held.changes.map((c, i) => (
        <div key={`${c.tool}-${i}`} className="flex flex-col gap-0.5">
          <span>
            <span className="font-mono font-bold text-foreground">{c.tool}</span>{" "}
            {CHANGE_TEXT[c.change] ? t(CHANGE_TEXT[c.change]) : c.change}
            {c.change === "changed" && c.schema_changed ? ` · ${t("mcp.held.params")}` : ""}
          </span>
          {c.duplicate && <span>{t("mcp.held.duplicate")}</span>}
          {c.change !== "added" && c.description_changed && c.old_description && (
            <span className="text-muted-foreground line-through">{c.old_description}</span>
          )}
          {c.change !== "removed" && c.description_changed && c.new_description && (
            <span className="text-foreground">{c.new_description}</span>
          )}
          {c.schema_changed && c.change !== "added" && c.old_schema && (
            <SchemaText label={t("mcp.held.oldParams")} text={c.old_schema} old />
          )}
          {c.schema_changed && c.change !== "removed" && c.new_schema && (
            <SchemaText label={t("mcp.held.newParams")} text={c.new_schema} />
          )}
          <CueLine cues={c.cues} t={t} />
        </div>
      ))}
      {stale && <span className="text-foreground">{t("mcp.held.stale")}</span>}
      {failed && <span className="text-bad-foreground">{t("mcp.held.failed")}</span>}
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={approving} onClick={onApprove}>
          {approving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("mcp.held.approve")}
        </Button>
        <span className="text-muted-foreground">{t("mcp.held.after")}</span>
      </div>
    </div>
  );
}

/** Per-server test state, keyed by name. `undefined` = never tested (no "connected" claim by default). */
type TestState = Record<string, { loading: boolean; result?: McpTest }>;

function EnvChips({ keys }: { keys: string[] }) {
  if (keys.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {keys.map((k) => (
        <span
          key={k}
          className="rounded-chip bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-muted-foreground ring-1 ring-hairline"
        >
          {k}
        </span>
      ))}
    </div>
  );
}

function ServerRow({
  server,
  state,
  onTest,
  onRemove,
  onApprove,
  approving = false,
  stale = false,
  approveFailed = false,
  approvedPendingRestart = false,
  t,
}: {
  server: McpServer;
  state?: { loading: boolean; result?: McpTest };
  onTest: () => void;
  onRemove: () => void;
  onApprove?: () => void;
  approving?: boolean;
  stale?: boolean;
  approveFailed?: boolean;
  approvedPendingRestart?: boolean;
  t: TFunc;
}) {
  const { lang } = useI18n();
  const result = state?.result;
  const cmd = [server.command, ...server.args].join(" ");
  // The remembered test, shown ONLY while this window has not tested the server itself. It is
  // history — "tested at 14:02, 4 tools" — and never the green "connected" badge: a test from
  // yesterday says nothing about whether the server starts today, and the badge is the one claim
  // this screen makes only after a real connect in this process.
  const last = result ? null : (server.last_test ?? null);
  const lastWhen = last ? new Date(last.tested_at * 1000).toLocaleString(lang) : "";
  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-foreground">{server.name}</span>
            {/* The green "connected" badge appears ONLY after a real, successful test — never by default. */}
            {result?.ok && (
              <Badge tone="ok">{t("mcp.connected", { n: result.tools.length })}</Badge>
            )}
          </div>
          <div className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{cmd}</div>
          <div className="mt-1.5">
            <EnvChips keys={server.env_keys} />
          </div>
          {last && (
            <div className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <History className="h-3.5 w-3.5 shrink-0" />
              <span>
                {last.ok
                  ? t("mcp.lastTest.ok", { when: lastWhen, n: last.tool_count })
                  : t("mcp.lastTest.failed", { when: lastWhen })}
              </span>
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button size="sm" variant="outline" disabled={state?.loading} onClick={onTest}>
            {state?.loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("mcp.test")}
          </Button>
          <button title={t("common.delete")} onClick={onRemove}>
            <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-bad" />
          </button>
        </div>
      </div>

      {server.manifest_held && (
        <HeldBlock
          held={server.manifest_held}
          onApprove={() => onApprove?.()}
          approving={approving}
          stale={stale}
          failed={approveFailed}
          t={t}
        />
      )}
      {/* Once approved the held block is gone, and with it the line that said the change takes
          effect on the next start. The pool connects once per process, so until the app restarts
          the server is still not connected — and without this line nothing on the screen said so. */}
      {!server.manifest_held && approvedPendingRestart && (
        <div className="flex items-start gap-1.5 rounded-chip border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn-foreground">
          <History className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{t("mcp.held.approved")}</span>
        </div>
      )}

      {/* "It works" and "the agent can use it" are different facts. A server can connect, list its
          tools, and still reach no run — autoload is off by default, and the servers are connected
          once per process. Measured: a server tested green and the next run made twenty-two tool
          calls over nineteen minutes without one of them being its. The green block below stays
          green, because the server DOES work; this line is what was missing beside it. */}
      {result?.reaches_agent === false && (
        <div className="flex items-start gap-1.5 rounded-chip border border-warn/30 bg-warn/10 px-3 py-2 text-xs text-warn-foreground">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            {result.reaches_agent_reason === "manifest_held"
              ? t("mcp.reach.manifestHeld")
              : result.reaches_agent_reason === "added_after_connect"
                ? t("mcp.reach.addedAfterConnect")
                : t("mcp.reach.autoloadOff")}
          </span>
        </div>
      )}
      {result && result.ok && (
        <div className="rounded-xl2 bg-ok/6 px-3 py-2 ring-1 ring-ok/15">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-ok-foreground">
            <Check className="h-3.5 w-3.5" /> {t("mcp.toolsExposed", { n: result.tools.length })}
          </div>
          <div className="flex flex-col gap-1">
            {result.tools.map((tool) => (
              <div key={tool.name} className="flex flex-col">
                <span className="font-mono text-xs font-bold text-foreground">{tool.name}</span>
                {tool.description && (
                  <span className="text-xs leading-snug text-muted-foreground">
                    {tool.description}
                  </span>
                )}
                <CueLine cues={tool.cues} t={t} />
              </div>
            ))}
          </div>
        </div>
      )}
      {result && !result.ok && (
        <div className="flex items-center gap-1.5 rounded-xl2 bg-bad/8 px-3 py-2 text-xs text-bad-foreground ring-1 ring-bad/20">
          <X className="h-3.5 w-3.5 shrink-0" /> {result.error ?? t("mcp.testFailed")}
        </div>
      )}
    </div>
  );
}

/** What a catalogue entry hands to the form. Empty for a hand-written server. */
export interface Prefill {
  name: string;
  command: string;
  args: string;
  envRows: { key: string; value: string }[];
  /** Picked from the catalogue — the only case that offers "Add and test". */
  fromCatalog: boolean;
  /** The env keys the entry declares as secrets: the form will not save while one is empty. */
  secretKeys: string[];
  /** The form each secret's value must have, by key, when the entry declares one. */
  secretPatterns: Record<string, string>;
}

const BLANK: Prefill = {
  name: "",
  command: "",
  args: "",
  envRows: [],
  fromCatalog: false,
  secretKeys: [],
  secretPatterns: {},
};

/** What the entry would write into `mcp.json`, in the form's own shape.
 *
 *  A secret becomes an EMPTY row rather than a placeholder value: the user has to type it, and an
 *  example sitting in the field is something somebody eventually saves by accident.
 */
function toPrefill(entry: McpCatalogEntry): Prefill {
  return {
    name: entry.id,
    command: entry.command,
    args: entry.args.join(" "),
    envRows: [
      ...Object.entries(entry.env).map(([key, value]) => ({ key, value })),
      ...entry.secrets.map((s) => ({ key: s.key, value: "" })),
    ],
    fromCatalog: true,
    secretKeys: entry.secrets.map((s) => s.key),
    secretPatterns: Object.fromEntries(
      entry.secrets.filter((s) => s.pattern).map((s) => [s.key, s.pattern as string]),
    ),
  };
}

/** The env map exactly as Add will send it: the LAST row of a key wins, and keys are trimmed.
 *
 *  The secret checks read this rather than the rows, because a check of the rows can pass on a
 *  value that is not the one saved: two rows of the same key, the first filled and the second
 *  empty, used to satisfy "some row has a value" and then save the empty one.
 */
function envFromRows(rows: { key: string; value: string }[]): Record<string, string> {
  const env: Record<string, string> = {};
  for (const row of rows) {
    if (row.key.trim()) env[row.key.trim()] = row.value;
  }
  return env;
}

/** Whether `value` has the declared form. A pattern that does not compile refuses everything:
 *  the alternative is to stop checking, and the check is what keeps a refused key from opening a
 *  whole-user sign-in. A broken pattern then shows up as an entry that cannot be saved. */
function hasForm(pattern: string, value: string): boolean {
  try {
    return new RegExp(pattern).test(value);
  } catch {
    return false;
  }
}

/** The entry's own words, in the reader's language.
 *
 *  The first version of this screen printed the backend's English straight onto a Portuguese page —
 *  the same defect as the hardcoded "Close" in the dialog, one release earlier and one layer up.
 *  The catalogue is data with machine facts in it (command, args, runner); the SENTENCES belong to
 *  the dictionary, keyed by entry id.
 *
 *  Written out rather than built with a template, because `i18n.reachable.test.ts` greps for each
 *  key as a literal and would list every one of these as dead. The five database entries share one
 *  pair of keys with the label interpolated — they differ only by which database they name.
 *
 *  The fallback is the backend's own text, so an entry added to the catalogue and not to the
 *  dictionary still says something rather than rendering blank.
 */
const ENTRY_TEXT: Record<string, { summary: string; containment: string }> = {
  github: { summary: "mcp.entry.github.summary", containment: "mcp.entry.github.containment" },
  "github-binary": {
    summary: "mcp.entry.githubBinary.summary",
    containment: "mcp.entry.githubBinary.containment",
  },
  firebase: { summary: "mcp.entry.firebase.summary", containment: "mcp.entry.firebase.containment" },
  supabase: { summary: "mcp.entry.supabase.summary", containment: "mcp.entry.supabase.containment" },
  stripe: { summary: "mcp.entry.stripe.summary", containment: "mcp.entry.stripe.containment" },
  notion: { summary: "mcp.entry.notion.summary", containment: "mcp.entry.notion.containment" },
  sentry: { summary: "mcp.entry.sentry.summary", containment: "mcp.entry.sentry.containment" },
  hostinger: {
    summary: "mcp.entry.hostinger.summary",
    containment: "mcp.entry.hostinger.containment",
  },
};

function CatalogCard({ entry, onPick }: { entry: McpCatalogEntry; onPick: () => void }) {
  const t = useT();
  const chaves = ENTRY_TEXT[entry.id];
  const ehBanco = entry.id.startsWith("db-");
  const summary = chaves
    ? t(chaves.summary)
    : ehBanco
      ? t("mcp.entry.db.summary", { n: entry.label })
      : entry.summary;
  const containment = chaves
    ? t(chaves.containment)
    : ehBanco
      ? t("mcp.entry.db.containment")
      : entry.containment;
  return (
    <div className="rounded-card border border-hairline p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-foreground">{entry.label}</span>
        <Badge tone={entry.official ? "accent" : "muted"}>
          {entry.official ? t("mcp.catalog.official") : t("mcp.catalog.community")}
        </Badge>
        {entry.docs ? (
          <a
            href={entry.docs}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ExternalLink className="h-3 w-3" /> {t("mcp.catalog.docs")}
          </a>
        ) : null}
      </div>
      <p className="mt-1 text-sm text-muted-foreground">{summary}</p>
      {/* The field this whole screen exists to be honest about. Not a badge: for most of these the
          limit is the CREDENTIAL, and a one-word "read-only" chip would say the opposite. */}
      <p className="mt-1.5 text-xs text-muted-foreground">{containment}</p>
      {entry.secrets.length ? (
        <p className="mt-1.5 text-xs text-muted-foreground">
          {t("mcp.catalog.asks", { n: entry.secrets.map((s) => s.key).join(", ") })}
        </p>
      ) : null}
      <div className="mt-2.5">
        {entry.available ? (
          <Button size="sm" variant="outline" onClick={onPick}>
            {t("mcp.catalog.use")}
          </Button>
        ) : (
          // Shown rather than hidden. "Install docker first" is actionable; an entry that silently
          // is not there teaches nothing, and one that IS there and then fails to connect teaches
          // the wrong thing.
          <span className="text-xs text-muted-foreground">
            {t("mcp.catalog.needs", { n: entry.runner })}
          </span>
        )}
      </div>
    </div>
  );
}

function AddForm({
  prefill,
  onAdded,
}: {
  prefill: Prefill;
  /** `test` is true only when the owner pressed "Add and test" — the click is the consent to run. */
  onAdded: (name: string, test: boolean) => void;
}) {
  const t = useT();
  // Seeded from the prefill rather than synced to it. The parent remounts this component with a
  // `key` per pick, which is the same device the run boards use — and it is what lets somebody
  // EDIT a prefilled value without the next render putting the catalogue's version back.
  const [name, setName] = useState(prefill.name);
  const [command, setCommand] = useState(prefill.command);
  const [args, setArgs] = useState(prefill.args);
  const [envRows, setEnvRows] = useState<{ key: string; value: string }[]>(prefill.envRows);

  const add = useMutation({
    mutationFn: (req: { body: Parameters<typeof addMcpServer>[0]; test: boolean }) =>
      addMcpServer(req.body),
    onSuccess: (_data, req) => {
      setName("");
      setCommand("");
      setArgs("");
      setEnvRows([]);
      onAdded(req.body.name, req.test);
    },
  });

  const submit = (test: boolean) => {
    const env = envFromRows(envRows);
    add.mutate({
      body: {
        name: name.trim(),
        command: command.trim(),
        args: args.trim() ? args.trim().split(/\s+/) : [],
        env,
      },
      test,
    });
  };

  // A secret the entry asks for, still empty. Saving without it is not a harmless half-step: the
  // Stripe entry would send an empty Authorization header, Stripe answers 401, and the mcp-remote
  // bridge answers THAT by opening Stripe's OAuth consent page — the whole-user grant the key is
  // there to avoid — and keeping the tokens in its own file. Plain Add is held too, because the
  // saved server would do the same on the first run that autoloads it.
  //
  // "Not empty" alone was not enough: the key pasted on its own — the natural copy from Stripe's
  // dashboard — is not empty, goes out with no "Bearer", and is refused the same way. So a secret
  // that declares a form has to have it too.
  const sentEnv = envFromRows(envRows);
  const missingSecrets = prefill.secretKeys.filter((k) => !(sentEnv[k] ?? "").trim());
  const malformedSecrets = prefill.secretKeys.filter(
    (k) =>
      !missingSecrets.includes(k) &&
      prefill.secretPatterns[k] !== undefined &&
      !hasForm(prefill.secretPatterns[k], sentEnv[k] ?? ""),
  );
  const canSubmit =
    name.trim().length > 0 &&
    command.trim().length > 0 &&
    missingSecrets.length === 0 &&
    malformedSecrets.length === 0 &&
    !add.isPending;

  return (
    <div className="flex flex-col gap-3 px-4 py-3">
      <div className="grid grid-cols-2 gap-2">
        <input
          className="field h-8 px-2.5 text-sm"
          placeholder={t("mcp.namePlaceholder")}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className="field h-8 px-2.5 text-sm"
          placeholder={t("mcp.commandPlaceholder")}
          value={command}
          onChange={(e) => setCommand(e.target.value)}
        />
      </div>
      <input
        className="field h-8 px-2.5 text-sm"
        placeholder={t("mcp.argsPlaceholder")}
        value={args}
        onChange={(e) => setArgs(e.target.value)}
      />
      {envRows.map((row, i) => (
        <div key={i} className="grid grid-cols-2 gap-2">
          <input
            className="field h-8 px-2.5 font-mono text-xs"
            placeholder={t("mcp.envKeyPlaceholder")}
            value={row.key}
            onChange={(e) =>
              setEnvRows((rows) => rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))
            }
          />
          <input
            className="field h-8 px-2.5 font-mono text-xs"
            type="password"
            placeholder={t("mcp.envValuePlaceholder")}
            value={row.value}
            onChange={(e) =>
              setEnvRows((rows) =>
                rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)),
              )
            }
          />
        </div>
      ))}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setEnvRows((rows) => [...rows, { key: "", value: "" }])}
        >
          <Plus className="mr-1 h-3.5 w-3.5" /> {t("mcp.addEnv")}
        </Button>
        <div className="flex-1" />
        {/* Offered only for a catalogue pick, and as its OWN button rather than a side effect of
            Add: testing starts the server's command on this machine (npx fetches a package), so
            the click that runs it has to say so. Add alone still only writes mcp.json. */}
        {prefill.fromCatalog && (
          <Button size="sm" variant="outline" disabled={!canSubmit} onClick={() => submit(true)}>
            {t("mcp.addAndTest")}
          </Button>
        )}
        <Button size="sm" disabled={!canSubmit} onClick={() => submit(false)}>
          {add.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("mcp.add")}
        </Button>
      </div>
      {missingSecrets.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {t("mcp.secretMissing", { n: missingSecrets.join(", ") })}
        </p>
      )}
      {malformedSecrets.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {t("mcp.secretFormat", { n: malformedSecrets.join(", ") })}
        </p>
      )}
      {prefill.fromCatalog && (
        <p className="text-xs text-muted-foreground">
          {t("mcp.addAndTest.note")} {t("mcp.addAndTest.signIn")}
        </p>
      )}
      {add.isError && <p className="text-xs text-bad-foreground">{t("mcp.addError")}</p>}
    </div>
  );
}

export function Mcp({ embedded = false }: { embedded?: boolean } = {}) {
  const t = useT();
  const qc = useQueryClient();
  const servers = useQuery({ queryKey: ["mcp"], queryFn: getMcpServers });
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const catalog = useQuery({ queryKey: ["mcp-catalog"], queryFn: getMcpCatalog });
  const [tests, setTests] = useState<TestState>({});
  // The chosen entry, and a counter that remounts the form. Two pieces rather than one, because
  // picking the SAME entry twice has to reset the form again — a key that never changes would
  // leave whatever the user had half-typed in place.
  const [prefill, setPrefill] = useState<Prefill>(BLANK);
  const [picked, setPicked] = useState(0);
  const [query, setQuery] = useState("");
  const [runner, setRunner] = useState<string | null>(null);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["mcp"] });
  const remove = useMutation({ mutationFn: removeMcpServer, onSuccess: invalidate });
  // Settled, not just success: a 409 means the held listing changed after it was rendered, and the
  // refetch is what puts the NEW diff on the screen for the owner to read before trying again.
  // The servers approved in this run of the app. Kept in the query cache, not component state, so
  // leaving the screen and coming back does not lose the "restart to connect" line; the cache dies
  // with the app, which is exactly when the line stops being true.
  const approvedThisRun = useQuery({
    queryKey: ["mcp-approved-this-run"],
    queryFn: () => [] as string[],
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const approve = useMutation({
    mutationFn: approveMcpManifest,
    onSuccess: (_list, { name }) =>
      qc.setQueryData<string[]>(["mcp-approved-this-run"], (prev = []) =>
        prev.includes(name) ? prev : [...prev, name],
      ),
    onSettled: invalidate,
  });
  const approveStatus = (name: string) =>
    approve.variables?.name === name && approve.isError
      ? ((approve.error as { status?: number } | null)?.status ?? 0)
      : null;
  const approveStale = (name: string) => approveStatus(name) === 409;
  // Anything else — a 503 while another process holds the pin file, a dropped connection — left the
  // button spinning and then nothing, since only the 409 had a line. Nothing was approved; say so.
  const approveFailed = (name: string) => {
    const status = approveStatus(name);
    return status !== null && status !== 409;
  };

  const runTest = async (name: string) => {
    setTests((s) => ({ ...s, [name]: { loading: true, result: s[name]?.result } }));
    try {
      const result = await testMcpServer(name);
      setTests((s) => ({ ...s, [name]: { loading: false, result } }));
      // The test was also remembered on disk; refetch so the list carries it once this window's
      // own result is gone (a relaunch, or the server removed and added back).
      invalidate();
    } catch {
      setTests((s) => ({
        ...s,
        [name]: { loading: false, result: { ok: false, tools: [], error: t("mcp.testFailed") } },
      }));
    }
  };

  const autoloadOff = config.data ? !config.data.mcp.autoload : false;

  const entries = useMemo(() => catalog.data?.entries ?? [], [catalog.data]);
  const runners = useMemo(() => [...new Set(entries.map((e) => e.runner))].sort(), [entries]);
  // Matched against the label, the id and the machine facts (command, args), not the translated
  // sentences: a search for "postgres" or "docker" should find the entry in every language, and
  // the sentences are the dictionary's, not the entry's.
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter(
      (e) =>
        (runner === null || e.runner === runner) &&
        (!q || [e.id, e.label, e.command, ...e.args].join(" ").toLowerCase().includes(q)),
    );
  }, [entries, query, runner]);

  if (servers.isError) {
    return (
      <Screen title={t("mcp.title")} icon={<Plug className="h-5 w-5" />} embedded={embedded}>
        <Panel>
          <ErrorState error={servers.error} onRetry={() => servers.refetch()} />
        </Panel>
      </Screen>
    );
  }
  if (servers.isLoading || !servers.data) {
    return (
      <Screen title={t("mcp.title")} icon={<Plug className="h-5 w-5" />} embedded={embedded}>
        <Panel>
          <Spinner />
        </Panel>
      </Screen>
    );
  }

  return (
    <Screen title={t("mcp.title")} icon={<Plug className="h-5 w-5" />} embedded={embedded}>
      {autoloadOff && (
        <div className="rounded-xl2 bg-surface-2 px-4 py-2.5 text-xs text-muted-foreground ring-1 ring-hairline">
          {t("mcp.autoloadOff")}
        </div>
      )}

      <Panel title={t("mcp.servers", { n: servers.data.count })}>
        {servers.data.count === 0 ? (
          <EmptyState text={t("mcp.empty")} />
        ) : (
          servers.data.servers.map((s) => (
            <ServerRow
              key={s.name}
              server={s}
              state={tests[s.name]}
              onTest={() => runTest(s.name)}
              onRemove={() => {
                setTests((prev) => {
                  const next = { ...prev };
                  delete next[s.name];
                  return next;
                });
                // Removing forgets the pin, so a server added back under this name is first sight
                // again, not an approval waiting on a restart.
                qc.setQueryData<string[]>(["mcp-approved-this-run"], (prev = []) =>
                  prev.filter((n) => n !== s.name),
                );
                remove.mutate(s.name);
              }}
              onApprove={() =>
                s.manifest_held && approve.mutate({ name: s.name, digest: s.manifest_held.digest })
              }
              approving={approve.isPending && approve.variables?.name === s.name}
              stale={approveStale(s.name)}
              approveFailed={approveFailed(s.name)}
              approvedPendingRestart={approvedThisRun.data?.includes(s.name) ?? false}
              t={t}
            />
          ))
        )}
      </Panel>

      {entries.length ? (
        <Panel title={t("mcp.catalog.title")}>
          <div className="flex flex-col gap-3 px-4 py-3">
            <p className="text-sm text-muted-foreground">{t("mcp.catalog.lead")}</p>
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder={t("mcp.catalog.search")}
                  aria-label={t("mcp.catalog.search")}
                  className="field h-8 w-full px-2.5 text-sm"
                />
              </div>
              <div role="group" aria-label={t("mcp.catalog.runner")} className="flex flex-wrap gap-1">
                <Button
                  size="sm"
                  variant={runner === null ? "outline" : "ghost"}
                  aria-pressed={runner === null}
                  onClick={() => setRunner(null)}
                >
                  {t("mcp.catalog.allRunners")}
                </Button>
                {runners.map((r) => (
                  <Button
                    key={r}
                    size="sm"
                    variant={runner === r ? "outline" : "ghost"}
                    aria-pressed={runner === r}
                    onClick={() => setRunner(runner === r ? null : r)}
                    className="font-mono"
                  >
                    {r}
                  </Button>
                ))}
              </div>
            </div>
            {shown.length === 0 && (
              <p className="text-sm text-muted-foreground">{t("mcp.catalog.noMatch")}</p>
            )}
            <div className="grid gap-2 lg:grid-cols-2">
              {shown.map((entry) => (
                <CatalogCard
                  key={entry.id}
                  entry={entry}
                  onPick={() => {
                    setPrefill(toPrefill(entry));
                    setPicked((n) => n + 1);
                  }}
                />
              ))}
            </div>
            {/* Said here, next to the entries that ask for one, rather than only in the footnote:
                a value typed into this screen lands in mcp.json as text. */}
            <p className="text-xs text-muted-foreground">{t("mcp.catalog.plaintext")}</p>
          </div>
        </Panel>
      ) : null}

      <Panel title={t("mcp.addServer")}>
        <AddForm
          key={picked}
          prefill={prefill}
          onAdded={(name, test) => {
            // Whatever this window last saw for that name was about the server just REPLACED —
            // maybe a different token under the same key names. Left in place, the row went on
            // showing the green "connected" for a configuration that never connected.
            setTests((prev) => {
              const next = { ...prev };
              delete next[name];
              return next;
            });
            invalidate();
            // Back to a blank, hand-written form: "Add and test" belonged to the pick just saved.
            setPrefill(BLANK);
            setPicked((n) => n + 1);
            // Never on boot, never on a hand-written server: only right after the owner pressed
            // "Add and test" on a catalogue pick, and only for the server that click saved.
            if (test) void runTest(name);
          }}
        />
      </Panel>

      <p className="px-1 text-xs text-muted-foreground">{t("mcp.note")}</p>
    </Screen>
  );
}
