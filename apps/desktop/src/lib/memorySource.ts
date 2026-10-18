import type { TFunc } from "@/lib/i18n";

/**
 * Where a stored fact came from, in words.
 *
 * The store's source ids ("chat", "extracted", "chimera") are identifiers, and Memory printed them
 * raw in a monospace font — "By source: chimera" read as jargon in every language. Profile already
 * had the words; this is that mapping, shared so the two screens cannot drift. A source this build
 * does not know is shown as sent rather than hidden: a newer server may add one.
 */
export function memorySourceLabel(t: TFunc, source: string): string {
  // Written out in full, not assembled, so the i18n reachability test can see each key.
  switch (source) {
    case "chat":
      return t("profile.source.chat");
    case "extracted":
      return t("profile.source.extracted");
    case "chimera":
      return t("profile.source.chimera");
    default:
      return source;
  }
}
