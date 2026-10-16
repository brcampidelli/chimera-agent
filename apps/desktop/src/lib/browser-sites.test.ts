import { describe, expect, it } from "vitest";

import { isBrowserSite, splitBrowserSites } from "@/lib/browser-sites";
import cases from "@/lib/browser-sites.cases.json";

// The same table `tests/test_the_settings_screen_checks_browser_sites_as_the_server_does.py` reads
// against `parse_sites`: a case can only be added once, and it is then checked on both sides.
describe("the browser site check agrees with the server", () => {
  it.each(cases.map((row) => [row.entry, row.accepted] as const))("%s → %s", (entry, accepted) => {
    expect(isBrowserSite(entry)).toBe(accepted);
  });

  it("splits a typed value the way the server does", () => {
    expect(splitBrowserSites(" github.com,  *.github.com\nsites.com ,")).toEqual([
      "github.com",
      "*.github.com",
      "sites.com",
    ]);
  });
});
