import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Trash2, Check, X, Loader2, Plus, ExternalLink } from "lucide-react";
import {
  addMcpServer,
  getConfig,
  getMcpCatalog,
  getMcpServers,
  removeMcpServer,
  testMcpServer,
} from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { ErrorState } from "@/components/ui/async";
import { Button } from "@/components/ui/button";
import { useT, type TFunc } from "@/lib/i18n";
import type { McpCatalogEntry } from "@/lib/api";
import type { McpServer, McpTest } from "@/lib/types";

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
  t,
}: {
  server: McpServer;
  state?: { loading: boolean; result?: McpTest };
  onTest: () => void;
  onRemove: () => void;
  t: TFunc;
}) {
  const result = state?.result;
  const cmd = [server.command, ...server.args].join(" ");
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

      {result && result.ok && (
        <div className="rounded-xl2 bg-ok/[0.06] px-3 py-2 ring-1 ring-ok/15">
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
              </div>
            ))}
          </div>
        </div>
      )}
      {result && !result.ok && (
        <div className="flex items-center gap-1.5 rounded-xl2 bg-bad/[0.08] px-3 py-2 text-xs text-bad-foreground ring-1 ring-bad/20">
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
}

const BLANK: Prefill = { name: "", command: "", args: "", envRows: [] };

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
  };
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
const ENTRY_TEXT: Record<
  string,
  { summary: "mcp.entry.github.summary" | "mcp.entry.githubBinary.summary" | "mcp.entry.firebase.summary" | "mcp.entry.supabase.summary"; containment: "mcp.entry.github.containment" | "mcp.entry.githubBinary.containment" | "mcp.entry.firebase.containment" | "mcp.entry.supabase.containment" }
> = {
  github: { summary: "mcp.entry.github.summary", containment: "mcp.entry.github.containment" },
  "github-binary": {
    summary: "mcp.entry.githubBinary.summary",
    containment: "mcp.entry.githubBinary.containment",
  },
  firebase: { summary: "mcp.entry.firebase.summary", containment: "mcp.entry.firebase.containment" },
  supabase: { summary: "mcp.entry.supabase.summary", containment: "mcp.entry.supabase.containment" },
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

function AddForm({ prefill, onAdded }: { prefill: Prefill; onAdded: () => void }) {
  const t = useT();
  // Seeded from the prefill rather than synced to it. The parent remounts this component with a
  // `key` per pick, which is the same device the run boards use — and it is what lets somebody
  // EDIT a prefilled value without the next render putting the catalogue's version back.
  const [name, setName] = useState(prefill.name);
  const [command, setCommand] = useState(prefill.command);
  const [args, setArgs] = useState(prefill.args);
  const [envRows, setEnvRows] = useState<{ key: string; value: string }[]>(prefill.envRows);

  const add = useMutation({
    mutationFn: addMcpServer,
    onSuccess: () => {
      setName("");
      setCommand("");
      setArgs("");
      setEnvRows([]);
      onAdded();
    },
  });

  const submit = () => {
    const env: Record<string, string> = {};
    for (const row of envRows) {
      if (row.key.trim()) env[row.key.trim()] = row.value;
    }
    add.mutate({
      name: name.trim(),
      command: command.trim(),
      args: args.trim() ? args.trim().split(/\s+/) : [],
      env,
    });
  };

  const canSubmit = name.trim().length > 0 && command.trim().length > 0 && !add.isPending;

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
        <Button size="sm" disabled={!canSubmit} onClick={submit}>
          {add.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("mcp.add")}
        </Button>
      </div>
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

  const invalidate = () => qc.invalidateQueries({ queryKey: ["mcp"] });
  const remove = useMutation({ mutationFn: removeMcpServer, onSuccess: invalidate });

  const runTest = async (name: string) => {
    setTests((s) => ({ ...s, [name]: { loading: true, result: s[name]?.result } }));
    try {
      const result = await testMcpServer(name);
      setTests((s) => ({ ...s, [name]: { loading: false, result } }));
    } catch {
      setTests((s) => ({
        ...s,
        [name]: { loading: false, result: { ok: false, tools: [], error: t("mcp.testFailed") } },
      }));
    }
  };

  const autoloadOff = config.data ? !config.data.mcp.autoload : false;

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
                remove.mutate(s.name);
              }}
              t={t}
            />
          ))
        )}
      </Panel>

      {catalog.data?.entries.length ? (
        <Panel title={t("mcp.catalog.title")}>
          <div className="flex flex-col gap-3 px-4 py-3">
            <p className="text-sm text-muted-foreground">{t("mcp.catalog.lead")}</p>
            <div className="grid gap-2 lg:grid-cols-2">
              {catalog.data.entries.map((entry) => (
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
        <AddForm key={picked} prefill={prefill} onAdded={invalidate} />
      </Panel>

      <p className="px-1 text-xs text-muted-foreground">{t("mcp.note")}</p>
    </Screen>
  );
}
