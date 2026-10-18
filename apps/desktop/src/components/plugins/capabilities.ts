import type { Plugin, PluginCapabilities } from "@/lib/types";

/**
 * What a plugin would DO, as the one line a card shows before anything else about it.
 *
 * The line exists because Claude's own "Will install" list does not say this well: a list of skill
 * names reads the same whether the plugin is three paragraphs of advice or a server that starts on
 * every launch. Four facts, in the order of how much they reach: words only, servers it could start
 * (added only if the owner ticks them), the network, and hooks or scripts that are kept and never run.
 */
export interface CapabilityFacts {
  /** Nothing but instructions. */
  textOnly: boolean;
  /** MCP servers it brings (each added only when the owner names it). */
  servers: number;
  /** A remote server: it would open a connection. */
  network: boolean;
  /** Hooks, monitors, bin/, workflows: stored, listed, never run. */
  inert: number;
  /** Unknown until installed: a GitHub marketplace entry is not downloaded to answer a listing. */
  unknown: boolean;
}

/** From an installed plugin, which knows its components one by one. */
export function factsOfPlugin(plugin: Plugin): CapabilityFacts {
  const components = plugin.components ?? [];
  const servers = components.filter((c) => c.kind === "mcp_server").length;
  const inert = components.filter((c) => c.inert).length;
  const network = components.some((c) => c.capability === "network");
  return { textOnly: servers === 0 && inert === 0 && !network, servers, network, inert, unknown: false };
}

/** From counts only (a marketplace entry read from disk), or nothing when they are not known. */
export function factsOfCounts(caps: PluginCapabilities | null | undefined): CapabilityFacts {
  if (!caps) return { textOnly: false, servers: 0, network: false, inert: 0, unknown: true };
  const process = caps.process ?? 0;
  const network = caps.network ?? 0;
  const exec = caps.exec ?? 0;
  return {
    textOnly: process === 0 && network === 0 && exec === 0,
    servers: process + network,
    network: network > 0,
    inert: exec,
    unknown: false,
  };
}

/** The seven characters of a pin a person can compare at a glance. */
export function shortPin(ref: string | null | undefined): string {
  return (ref ?? "").slice(0, 7);
}
