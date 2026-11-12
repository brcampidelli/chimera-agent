/** Why a Code turn did not read the prompt cache: the fixed causes of `chimera/core/cache_miss.py`,
 *  as the i18n keys that name them on screen.
 *
 *  A cause is shown only for a turn that read nothing from the cache. A turn that read something has
 *  no miss to explain, and a silent route (null) has no answer to give, so neither draws a cause. */

/** Literal keys, not a template: the dictionary test finds a key only where its full name is written. */
const MISS_LABEL_KEYS: Record<string, string> = {
  first_turn: "code.chat.miss.first_turn",
  ttl_expired: "code.chat.miss.ttl_expired",
  prefix_changed: "code.chat.miss.prefix_changed",
  tools_changed: "code.chat.miss.tools_changed",
  model_or_route_changed: "code.chat.miss.model_or_route_changed",
  provider_no_cache: "code.chat.miss.provider_no_cache",
  unknown: "code.chat.miss.unknown",
};

export function missLabelKey(cause: string): string {
  return MISS_LABEL_KEYS[cause] ?? "code.chat.miss.unknown";
}

export function shownMissCause(
  cause: string | null | undefined,
  cacheRead: number | null | undefined,
): string | null {
  if (cacheRead !== 0 || !cause || cause === "none") return null;
  return cause;
}
