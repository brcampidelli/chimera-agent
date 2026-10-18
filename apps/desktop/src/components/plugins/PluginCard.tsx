import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Trash2 } from "lucide-react";

import {
  applyPluginUpdate,
  checkPluginUpdate,
  disablePlugin,
  enablePlugin,
  getPluginSuggestions,
  removePlugin,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { focusRing } from "@/components/ui/focus";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { Plugin } from "@/lib/types";
import { EnableDialog } from "@/components/plugins/EnableDialog";
import { CapabilityLine } from "@/components/catalog/CapabilityLine";
import { ComponentList, DiffBlock, PluginIcon, StatusBadge } from "@/components/plugins/PluginParts";
import { factsOfPlugin, shortPin } from "@/components/plugins/capabilities";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Update in two steps, because they are two decisions: see what changed, then take it. Taking it
 *  switches the plugin off and holds it — what the owner reviewed is not what is there now. */
function UpdateControl({ plugin, onChanged }: { plugin: Plugin; onChanged: () => void }) {
  const t = useT();
  const check = useMutation({ mutationFn: () => checkPluginUpdate(plugin.name) });
  const apply = useMutation({
    mutationFn: () => applyPluginUpdate(plugin.name),
    onSuccess: () => {
      check.reset();
      onChanged();
    },
  });
  const found = check.data;
  const failed = check.error ?? apply.error;
  return (
    <div className="space-y-1">
      <Button size="sm" variant="ghost" disabled={check.isPending} onClick={() => check.mutate()}>
        {check.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
        {t("catalog.update.check")}
      </Button>
      {found && !found.changed ? <p className="text-xs text-muted-foreground">{t("plugins.update.same")}</p> : null}
      {found?.changed && found.diff ? (
        <div className="space-y-2">
          <DiffBlock diff={found.diff} title={t("plugins.update.changed")} />
          <Button size="sm" variant="outline" disabled={apply.isPending} onClick={() => apply.mutate()}>
            {apply.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("plugins.update.apply")}
          </Button>
        </div>
      ) : null}
      {failed ? <p className="text-xs text-bad-foreground">{errorText(failed)}</p> : null}
    </div>
  );
}

/** The hook files of a plugin, readable so the owner can copy one into their own hooks by hand.
 *  Read on request only: it is a stranger's commands, shown as text and never loaded. */
function Suggestions({ name }: { name: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const found = useQuery({
    queryKey: ["plugin-suggestions", name],
    queryFn: () => getPluginSuggestions(name),
    enabled: open,
  });
  return (
    <details className="text-xs" onToggle={(event) => setOpen((event.target as HTMLDetailsElement).open)}>
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        {t("plugins.suggestions")}
      </summary>
      {(found.data?.suggestions ?? []).map((s) => (
        <div key={`${s.kind}:${s.name}:${s.path}`} className="mt-1">
          <p className="font-mono text-muted-foreground">{`${s.kind} · ${s.path}`}</p>
          {s.text ? (
            <pre className="max-h-48 overflow-y-auto whitespace-pre-wrap wrap-break-word rounded-chip bg-surface-2 p-2 text-muted-foreground">
              {s.text}
            </pre>
          ) : null}
        </div>
      ))}
    </details>
  );
}

export function PluginCard({ plugin }: { plugin: Plugin }) {
  const t = useT();
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const refresh = () => void client.invalidateQueries({ queryKey: ["plugins"] });
  const enable = useMutation({
    mutationFn: (servers: string[]) => enablePlugin(plugin.name, servers),
    onSuccess: () => {
      setConfirming(false);
      refresh();
    },
  });
  const disable = useMutation({ mutationFn: () => disablePlugin(plugin.name), onSuccess: refresh });
  const remove = useMutation({
    mutationFn: () => removePlugin(plugin.name),
    onSuccess: () => {
      setRemoving(false);
      refresh();
    },
  });

  const label = plugin.display_name || plugin.name;
  const on = plugin.status === "active";
  const inert = (plugin.components ?? []).some((c) => c.inert);
  const pin = shortPin(plugin.ref || plugin.tree_sha256);

  return (
    <article className="surface flex flex-col gap-2 p-4" aria-label={label}>
      <div className="flex items-start gap-3">
        <PluginIcon />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">{label}</h3>
            {plugin.version ? <span className="text-xs text-muted-foreground">{plugin.version}</span> : null}
            <StatusBadge status={plugin.status ?? "pending"} denied={plugin.denied} />
          </div>
          {plugin.description ? <p className="text-xs text-muted-foreground">{plugin.description}</p> : null}
          <p className="text-xs text-muted-foreground">
            {[plugin.author ? t("catalog.by", { author: plugin.author }) : "", plugin.source, pin]
              .filter(Boolean)
              .join(" · ")}
          </p>
          <CapabilityLine facts={factsOfPlugin(plugin)} />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Switch
            checked={on}
            disabled={plugin.denied || enable.isPending || disable.isPending}
            label={t("plugins.switch", { name: label })}
            onChange={(next) => (next ? setConfirming(true) : disable.mutate())}
          />
          <button
            type="button"
            aria-label={t("plugins.remove", { name: label })}
            onClick={() => setRemoving(true)}
            className={cn(
              "rounded-md p-1 text-muted-foreground transition-colors duration-1 ease-out hover:text-bad",
              focusRing,
            )}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {plugin.status === "held" && plugin.held_diff ? (
        <div className="space-y-2">
          <DiffBlock diff={plugin.held_diff} title={t("plugins.held.title")} />
          <Button size="sm" variant="outline" disabled={plugin.denied} onClick={() => setConfirming(true)}>
            {t("plugins.review")}
          </Button>
        </div>
      ) : null}

      <ComponentList components={plugin.components ?? []} />
      {inert ? <Suggestions name={plugin.name} /> : null}
      <UpdateControl plugin={plugin} onChanged={refresh} />
      {disable.error ? <p className="text-xs text-bad-foreground">{errorText(disable.error)}</p> : null}

      <EnableDialog
        plugin={plugin}
        open={confirming}
        onOpenChange={setConfirming}
        onConfirm={(servers) => enable.mutate(servers)}
        pending={enable.isPending}
        error={enable.error}
      />
      <Dialog
        open={removing}
        onOpenChange={setRemoving}
        title={t("plugins.remove.title", { name: label })}
        description={t("plugins.remove.body")}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={() => setRemoving(false)}>
              {t("common.cancel")}
            </Button>
            <Button size="sm" disabled={remove.isPending} onClick={() => remove.mutate()}>
              {t("plugins.remove.confirm")}
            </Button>
          </>
        }
      >
        {remove.error ? <p className="text-xs text-bad-foreground">{errorText(remove.error)}</p> : null}
      </Dialog>
    </article>
  );
}
