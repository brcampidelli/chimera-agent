/**
 * The site-list syntax of `CHIMERA_BROWSER_SITES`, checked the way `parse_sites` in
 * `chimera/tools/browser_reach.py` checks it, so the screen does not send a PATCH the server will
 * refuse. The server stays the authority: anything this misses is refused there and shown as the
 * save error.
 *
 * The first version of this check put `\\s` inside a character class of a regex literal, which is a
 * backslash and the letter "s": every host with an "s" in it (`sites.com`, `docs.github.com`) was
 * refused on the screen while the server would have taken it.
 */

/** `_SITE` in `browser_reach.py`, character for character. */
const SITE = /^(?:\*\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$/;

/** What `ascii_host` does: lowercase, drop trailing dots, and punycode a non-ASCII name. */
function asciiHost(host: string): string {
  const lowered = host.trim().toLowerCase().replace(/\.+$/, "");
  if (/^[\x00-\x7f]*$/.test(lowered)) return lowered;
  // A name with URL syntax in it is not one the IDNA encoder takes; the server keeps it as written
  // and the pattern then refuses it. Handing it to `URL` would instead read the syntax.
  if (/[/:?#@\\%\s]/.test(lowered)) return lowered;
  try {
    return new URL(`http://${lowered}`).hostname;
  } catch {
    return lowered;
  }
}

/** The entries of a typed value, split as `_entries` splits them: on commas and whitespace. */
export function splitBrowserSites(raw: string): string[] {
  return raw.split(/[,\s]+/).map((part) => part.trim()).filter(Boolean);
}

/** Whether one entry is a site the server's list accepts (`example.com`, `*.example.com`). */
export function isBrowserSite(entry: string): boolean {
  const wild = entry.startsWith("*.");
  return SITE.test((wild ? "*." : "") + asciiHost(wild ? entry.slice(2) : entry));
}
