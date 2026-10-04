import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { Badge, EmptyState, Panel, Spinner } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import {
  addConnector,
  getConnectors,
  patchConnector,
  removeConnector,
  setConnectorKey,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { Connector, ConnectorPatch } from "@/lib/types";

const fieldCls = "field h-8 px-2.5 text-sm";

/** The server's own sentence when it refused (`detail`), else nothing — a refusal here is always
 *  one of the store's plain-English reasons, never a remote body. */
function why(error: unknown): string {
  return error instanceof Error ? error.message : "";
}

/**
 * OpenAPI connectors (study 29, P7.5): an HTTP API's spec turned into tools the agent can call.
 *
 * Everything that widens what the agent reaches is the owner's decision, made here and nowhere
 * else — these routes are absent from the desktop bridge. A connector arrives switched off with only
 * its GET operations ticked. An operation that changes data cannot be ticked until "allow changes" is
 * on, and even then each call asks on a card; where nobody can be asked (the scheduler, the bots)
 * it is refused. Those unattended surfaces load a connector only when the owner also sends it there,
 * and no bot loads one while any bot answers anyone. The key goes in once and comes back as its last
 * four characters at most.
 */
export function OpenApiConnectors() {
  const t = useT();
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["connectors"], queryFn: () => getConnectors(), retry: false });
  const refresh = () => qc.invalidateQueries({ queryKey: ["connectors"] });

  return (
    <div className="flex flex-col gap-4">
      <p className="px-1 text-xs text-muted-foreground">{t("connectors.intro")}</p>
      {list.isError && (
        <p role="status" className="px-1 text-xs text-bad-foreground">
          {t("connectors.unavailable")}
        </p>
      )}
      {list.isLoading && <Spinner />}
      {list.data && (
        <Panel title={t("connectors.title", { n: list.data.connectors.length })}>
          {list.data.connectors.length === 0 ? (
            <EmptyState text={t("connectors.empty")} />
          ) : (
            list.data.connectors.map((c) => <ConnectorRow key={c.name} connector={c} onChanged={refresh} />)
          )}
        </Panel>
      )}
      <Panel title={t("connectors.addTitle")}>
        <AddConnector onAdded={refresh} />
      </Panel>
      {list.data && (
        <p className="px-1 text-xs text-muted-foreground">
          {t("connectors.file", { path: list.data.store })}
        </p>
      )}
    </div>
  );
}

function AddConnector({ onAdded }: { onAdded: () => void }) {
  const t = useT();
  const [name, setName] = useState("");
  const [source, setSource] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const add = useMutation({
    mutationFn: () =>
      addConnector({ name: name.trim(), source: source.trim(), base_url: baseUrl.trim() || null }),
    onSuccess: () => {
      setName("");
      setSource("");
      setBaseUrl("");
      onAdded();
    },
  });
  const ready = name.trim() !== "" && source.trim() !== "" && !add.isPending;

  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      <div className="grid grid-cols-2 gap-2">
        <input
          className={fieldCls}
          aria-label={t("connectors.name")}
          placeholder={t("connectors.name")}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <input
          className={fieldCls}
          aria-label={t("connectors.source")}
          placeholder={t("connectors.source")}
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
      </div>
      <input
        className={fieldCls}
        aria-label={t("connectors.baseUrl")}
        placeholder={t("connectors.baseUrl")}
        value={baseUrl}
        onChange={(e) => setBaseUrl(e.target.value)}
      />
      <div className="flex items-center justify-end gap-2">
        {add.isError && <p className="flex-1 text-xs text-bad-foreground">{why(add.error)}</p>}
        <Button size="sm" disabled={!ready} onClick={() => add.mutate()}>
          {add.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : t("common.add")}
        </Button>
      </div>
    </div>
  );
}

function ConnectorRow({ connector: c, onChanged }: { connector: Connector; onChanged: () => void }) {
  const t = useT();
  const [confirming, setConfirming] = useState(false);
  const patch = useMutation({
    mutationFn: (change: ConnectorPatch) => patchConnector(c.name, change),
    onSuccess: onChanged,
  });
  const remove = useMutation({ mutationFn: () => removeConnector(c.name), onSuccess: onChanged });
  const toggle = (id: string, on: boolean) => {
    const selected = c.operations.filter((op) => op.selected).map((op) => op.id);
    patch.mutate({ operations: on ? [...selected, id] : selected.filter((x) => x !== id) });
  };

  return (
    <div className="flex flex-col gap-3 px-4 py-3">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold">{c.name}</span>
            <Badge tone={c.enabled ? "ok" : "muted"}>
              {c.enabled ? t("connectors.on") : t("connectors.off")}
            </Badge>
          </div>
          <p className="truncate font-mono text-xs text-muted-foreground" title={c.source}>
            {t("connectors.reaches", { url: c.base_url })}
          </p>
        </div>
        <Switch
          checked={c.enabled}
          onChange={(next) => patch.mutate({ enabled: next })}
          label={t("connectors.enable", { name: c.name })}
          // A broken connector can always be switched OFF; only switching it on needs its spec.
          disabled={c.problem !== "" && !c.enabled}
        />
      </div>
      {c.problem && <p className="text-xs text-warn-foreground">{c.problem}</p>}
      {patch.isError && <p className="text-xs text-bad-foreground">{why(patch.error)}</p>}

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm">{t("connectors.allowWrites")}</p>
          <p className="text-xs text-muted-foreground">{t("connectors.allowWritesHint")}</p>
        </div>
        <Switch
          checked={c.allow_writes}
          onChange={(next) => patch.mutate({ allow_writes: next })}
          label={t("connectors.allowWrites")}
        />
      </div>

      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm">{t("connectors.unattended")}</p>
          <p className="text-xs text-muted-foreground">{t("connectors.unattendedHint")}</p>
        </div>
        <Switch
          checked={c.unattended}
          onChange={(next) => patch.mutate({ unattended: next })}
          label={t("connectors.unattended")}
        />
      </div>

      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-xs font-semibold text-muted-foreground">
          {t("connectors.operations")}
        </legend>
        {c.operations.map((op) => {
          const locked = op.method !== "GET" && !c.allow_writes;
          return (
            <label key={op.id} className="flex items-baseline gap-2 text-sm">
              <input
                type="checkbox"
                checked={op.selected}
                disabled={locked || patch.isPending}
                onChange={(e) => toggle(op.id, e.target.checked)}
              />
              <span className="font-mono text-xs">{op.method}</span>
              <span className="min-w-0 truncate font-mono text-xs" title={op.summary}>
                {op.path}
              </span>
              {locked && <span className="text-xs text-muted-foreground">{t("connectors.needsWrites")}</span>}
            </label>
          );
        })}
      </fieldset>

      <KeyEditor connector={c} onChanged={onChanged} />

      <div className="flex items-center justify-end gap-2">
        {confirming ? (
          <>
            <p className="flex-1 text-xs text-muted-foreground">
              {t("connectors.removeConfirm", { name: c.name })}
            </p>
            <Button size="sm" variant="outline" onClick={() => setConfirming(false)}>
              {t("common.cancel")}
            </Button>
            <Button size="sm" onClick={() => remove.mutate()}>
              {t("connectors.remove")}
            </Button>
          </>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
            {t("connectors.remove")}
          </Button>
        )}
      </div>
    </div>
  );
}

/** Where the key comes from and how it is sent. The value is typed once and never shown again. */
function KeyEditor({ connector: c, onChanged }: { connector: Connector; onChanged: () => void }) {
  const t = useT();
  const [value, setValue] = useState("");
  const [keyIn, setKeyIn] = useState<"header" | "query">(c.key_in === "query" ? "query" : "header");
  const [keyName, setKeyName] = useState(c.key_name);
  const [prefix, setPrefix] = useState(c.key_prefix);
  const save = useMutation({
    mutationFn: () => setConnectorKey(c.name, value),
    onSuccess: () => {
      setValue("");
      onChanged();
    },
  });
  const style = useMutation({
    mutationFn: (change: ConnectorPatch) => patchConnector(c.name, change),
    onSuccess: onChanged,
  });

  return (
    <div className="flex flex-col gap-2 rounded-xl2 bg-surface-2 px-3 py-2.5 ring-1 ring-hairline">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold">{t("connectors.key")}</span>
        <select
          className="field h-7 px-2 font-mono text-xs"
          aria-label={t("connectors.keyFrom")}
          value={c.key_env}
          onChange={(e) => style.mutate({ key_env: e.target.value })}
        >
          {c.key_envs.map((env) => (
            <option key={env} value={env}>
              {env}
            </option>
          ))}
        </select>
        <span className="text-muted-foreground">
          {c.key_set ? t("connectors.keySet", { hint: c.key_hint }) : t("connectors.keyUnset")}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <input
          className={`${fieldCls} flex-1 font-mono`}
          type="password"
          autoComplete="off"
          aria-label={t("connectors.keyPlaceholder")}
          placeholder={t("connectors.keyPlaceholder")}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
        <Button size="sm" variant="outline" disabled={!value.trim() || save.isPending} onClick={() => save.mutate()}>
          {t("connectors.saveKey")}
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="field h-8 px-2 text-xs"
          aria-label={t("connectors.keyIn")}
          value={keyIn}
          onChange={(e) => setKeyIn(e.target.value === "query" ? "query" : "header")}
        >
          <option value="header">{t("connectors.keyInHeader")}</option>
          <option value="query">{t("connectors.keyInQuery")}</option>
        </select>
        <input
          className={`${fieldCls} w-40 font-mono`}
          aria-label={t("connectors.keyName")}
          placeholder={t("connectors.keyName")}
          value={keyName}
          onChange={(e) => setKeyName(e.target.value)}
        />
        {keyIn === "header" && (
          <input
            className={`${fieldCls} w-28 font-mono`}
            aria-label={t("connectors.keyPrefix")}
            placeholder={t("connectors.keyPrefix")}
            value={prefix}
            onChange={(e) => setPrefix(e.target.value)}
          />
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={style.isPending}
          onClick={() => style.mutate({ key_in: keyIn, key_name: keyName.trim(), key_prefix: prefix })}
        >
          {t("common.save")}
        </Button>
      </div>
      {(save.isError || style.isError) && (
        <p className="text-xs text-bad-foreground">{why(save.error ?? style.error)}</p>
      )}
    </div>
  );
}
