import { useState } from "react";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { useT } from "@/lib/i18n";
import type { Plugin } from "@/lib/types";

/**
 * The owner switching a plugin on — the one decision this screen exists to put in front of them.
 *
 * Said in full before the click, because the click is the whole consent: which texts will reach the
 * agent (namespaced and marked untrusted), which MCP servers could be added — each its own box, none
 * ticked, since a server starts a process or opens a connection — and what stays on disk and never
 * runs. The bridge cannot reach the route behind the button at any tier; nothing else switches one on.
 */
export function EnableDialog({
  plugin,
  open,
  onOpenChange,
  onConfirm,
  pending,
  error,
}: {
  plugin: Plugin;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: (servers: string[]) => void;
  pending: boolean;
  error: unknown;
}) {
  const t = useT();
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const components = plugin.components ?? [];
  const reaching = components.filter((c) => c.reaches_agent);
  const inert = components.filter((c) => c.inert);
  const servers = plugin.servers ?? [];
  const label = plugin.display_name || plugin.name;

  const toggle = (name: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setPicked(new Set());
        onOpenChange(next);
      }}
      title={t("plugins.enable.title", { name: label })}
      footer={
        <>
          <Button variant="ghost" size="sm" onClick={() => onOpenChange(false)}>
            {t("common.cancel")}
          </Button>
          <Button size="sm" disabled={pending} onClick={() => onConfirm([...picked])}>
            {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("plugins.enable.confirm")}
          </Button>
        </>
      }
    >
      <div className="max-h-96 space-y-3 overflow-y-auto text-sm">
        <section aria-label={t("plugins.enable.reaches")}>
          <p className="text-muted-foreground">
            {reaching.length > 0 ? t("plugins.enable.reaches") : t("plugins.enable.none")}
          </p>
          <ul className="mt-1 space-y-0.5 pl-3 text-xs">
            {reaching.map((c) => (
              <li key={`${c.kind}:${c.name}`}>
                <span className="font-mono">{`${plugin.name}:${c.name}`}</span>{" "}
                <span className="text-muted-foreground">{c.kind}</span>
              </li>
            ))}
          </ul>
        </section>

        {servers.length > 0 ? (
          <fieldset>
            <legend className="text-muted-foreground">{t("plugins.enable.servers")}</legend>
            <div className="mt-1 space-y-1 pl-1 text-xs">
              {servers.map((s) => (
                <label key={s.name} className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={picked.has(s.name)}
                    onChange={(event) => toggle(s.name, event.target.checked)}
                  />
                  <span>
                    <span className="font-mono">{s.name}</span>
                    <span className="text-muted-foreground">{` → ${s.configured_as}`}</span>
                    {s.detail && s.detail.length > 0 ? (
                      <span className="block font-mono text-muted-foreground">{s.detail.join(" ")}</span>
                    ) : null}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        ) : null}

        {inert.length > 0 ? (
          <section aria-label={t("plugins.enable.inert")}>
            <p className="text-muted-foreground">{t("plugins.enable.inert")}</p>
            <ul className="mt-1 space-y-0.5 pl-3 text-xs text-muted-foreground">
              {inert.map((c) => (
                <li key={`${c.kind}:${c.name}:${c.path}`}>
                  <span className="font-mono">{c.name}</span> {c.kind}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        {error ? (
          <p role="alert" className="text-xs text-bad-foreground">
            {error instanceof Error ? error.message : String(error)}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
