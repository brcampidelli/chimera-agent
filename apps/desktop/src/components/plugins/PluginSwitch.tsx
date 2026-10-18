import { useState, type ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { disablePlugin, enablePlugin } from "@/lib/api";
import { Switch } from "@/components/ui/switch";
import { useT } from "@/lib/i18n";
import type { Plugin } from "@/lib/types";
import { EnableDialog } from "@/components/plugins/EnableDialog";

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export interface PluginToggle {
  /** The switch: off needs no question, on opens the review first. */
  control: ReactNode;
  /** The review (`EnableDialog`), mounted once wherever the switch is. */
  dialog: ReactNode;
  /** Open the review directly — "Review and switch on". */
  review: () => void;
  /** A failed switch-off, as a line of text. */
  error: ReactNode;
}

/**
 * Switching a plugin on or off, shared by the card in Mine and the detail panel so the two can never
 * disagree about what switching on asks. On is never one click: it opens `EnableDialog`, which lists
 * what reaches the agent and leaves every server unticked. The route behind it is the owner's alone —
 * the bridge cannot reach it at any tier, and nothing here changes that.
 */
export function usePluginToggle(plugin: Plugin): PluginToggle {
  const t = useT();
  const client = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["plugins"] });
    void client.invalidateQueries({ queryKey: ["plugin-market"] });
  };
  const enable = useMutation({
    mutationFn: (servers: string[]) => enablePlugin(plugin.name, servers),
    onSuccess: () => {
      setConfirming(false);
      refresh();
    },
  });
  const disable = useMutation({ mutationFn: () => disablePlugin(plugin.name), onSuccess: refresh });
  const label = plugin.display_name || plugin.name;

  return {
    control: plugin.denied ? null : (
      <Switch
        checked={plugin.status === "active"}
        disabled={enable.isPending || disable.isPending}
        label={t("plugins.switch", { name: label })}
        onChange={(next) => (next ? setConfirming(true) : disable.mutate())}
      />
    ),
    dialog: (
      <EnableDialog
        plugin={plugin}
        open={confirming}
        onOpenChange={setConfirming}
        onConfirm={(servers) => enable.mutate(servers)}
        pending={enable.isPending}
        error={enable.error}
      />
    ),
    review: () => setConfirming(true),
    error: disable.error ? <p className="text-xs text-bad-foreground">{errorText(disable.error)}</p> : null,
  };
}

/** The switch as a card draws it, beside the state. */
export function PluginSwitch({ plugin }: { plugin: Plugin }) {
  const toggle = usePluginToggle(plugin);
  return (
    <>
      {toggle.control}
      {toggle.error}
      {toggle.dialog}
    </>
  );
}
