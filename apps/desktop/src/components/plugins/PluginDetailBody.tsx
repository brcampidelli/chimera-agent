import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw, Trash2 } from "lucide-react";

import { applyPluginUpdate, checkPluginUpdate, getPluginSuggestions, removePlugin } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";
import type { Plugin } from "@/lib/types";
import { CapabilityLine } from "@/components/catalog/CapabilityLine";
import { ComponentList, DiffBlock, StatusBadge } from "@/components/plugins/PluginParts";
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

/**
 * What used to be the plugin's card, now the body of its detail panel (study 04, §2.7): full
 * description, author, source and pin, what it would run, the change it is held on, its components,
 * the hook files to read, the update check and removal. Switching on is NOT here: it is the last of
 * the panel's steps, so it reads as the decision it is rather than as one control among many.
 */
export function PluginDetailBody({ plugin, onRemoved }: { plugin: Plugin; onRemoved?: () => void }) {
  const t = useT();
  const client = useQueryClient();
  const [removing, setRemoving] = useState(false);
  const refresh = () => void client.invalidateQueries({ queryKey: ["plugins"] });
  const remove = useMutation({
    mutationFn: () => removePlugin(plugin.name),
    onSuccess: () => {
      setRemoving(false);
      refresh();
      void client.invalidateQueries({ queryKey: ["plugin-market"] });
      onRemoved?.();
    },
  });

  const label = plugin.display_name || plugin.name;
  const inert = (plugin.components ?? []).some((c) => c.inert);
  const pin = shortPin(plugin.ref || plugin.tree_sha256);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={plugin.status ?? "pending"} denied={plugin.denied} />
        {plugin.version ? <span className="text-xs text-muted-foreground">{plugin.version}</span> : null}
      </div>
      {plugin.description ? <p className="text-sm text-foreground">{plugin.description}</p> : null}
      <p className="text-xs text-muted-foreground">
        {[plugin.author ? t("catalog.by", { author: plugin.author }) : "", plugin.source, pin]
          .filter(Boolean)
          .join(" · ")}
      </p>
      <CapabilityLine facts={factsOfPlugin(plugin)} />

      {plugin.status === "held" && plugin.held_diff ? (
        <DiffBlock diff={plugin.held_diff} title={t("plugins.held.title")} />
      ) : null}

      <ComponentList components={plugin.components ?? []} />
      {inert ? <Suggestions name={plugin.name} /> : null}
      <UpdateControl plugin={plugin} onChanged={refresh} />

      <div>
        <Button size="sm" variant="ghost" onClick={() => setRemoving(true)}>
          <Trash2 aria-hidden className="h-4 w-4" />
          {t("plugins.remove", { name: label })}
        </Button>
      </div>
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
    </div>
  );
}
