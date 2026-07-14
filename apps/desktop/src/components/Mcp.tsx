import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug, Trash2, Check, X, Loader2, Plus } from "lucide-react";
import {
  addMcpServer,
  getConfig,
  getMcpServers,
  removeMcpServer,
  testMcpServer,
} from "@/lib/api";
import { Badge, EmptyState, Panel, Screen, Spinner } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { useT, type TFunc } from "@/lib/i18n";
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
          className="rounded-chip bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground ring-1 ring-white/5"
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
          <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-ok">
            <Check className="h-3.5 w-3.5" /> {t("mcp.toolsExposed", { n: result.tools.length })}
          </div>
          <div className="flex flex-col gap-1">
            {result.tools.map((tool) => (
              <div key={tool.name} className="flex flex-col">
                <span className="font-mono text-[11px] font-bold text-foreground">{tool.name}</span>
                {tool.description && (
                  <span className="text-[11px] leading-snug text-muted-foreground">
                    {tool.description}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      {result && !result.ok && (
        <div className="flex items-center gap-1.5 rounded-xl2 bg-bad/[0.08] px-3 py-2 text-xs text-bad ring-1 ring-bad/20">
          <X className="h-3.5 w-3.5 shrink-0" /> {result.error ?? t("mcp.testFailed")}
        </div>
      )}
    </div>
  );
}

function AddForm({ onAdded }: { onAdded: () => void }) {
  const t = useT();
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [args, setArgs] = useState("");
  const [envRows, setEnvRows] = useState<{ key: string; value: string }[]>([]);

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
      {add.isError && <p className="text-xs text-bad">{t("mcp.addError")}</p>}
    </div>
  );
}

export function Mcp() {
  const t = useT();
  const qc = useQueryClient();
  const servers = useQuery({ queryKey: ["mcp"], queryFn: getMcpServers });
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const [tests, setTests] = useState<TestState>({});

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

  if (servers.isLoading || !servers.data) {
    return (
      <Screen title={t("mcp.title")} icon={<Plug className="h-5 w-5" />}>
        <Panel>
          <Spinner />
        </Panel>
      </Screen>
    );
  }

  return (
    <Screen title={t("mcp.title")} icon={<Plug className="h-5 w-5" />}>
      {autoloadOff && (
        <div className="rounded-xl2 bg-white/[0.03] px-4 py-2.5 text-xs text-muted-foreground ring-1 ring-white/5">
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

      <Panel title={t("mcp.addServer")}>
        <AddForm onAdded={invalidate} />
      </Panel>

      <p className="px-1 text-[11px] text-muted-foreground">{t("mcp.note")}</p>
    </Screen>
  );
}
