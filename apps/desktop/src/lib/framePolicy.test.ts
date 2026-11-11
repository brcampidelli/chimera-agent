import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

// The page's own frame rule, read from the file the browser reads. The backend's header also says
// `frame-src 'none'`; this test pins the meta that stands behind it for a document without that header.
const indexHtml = readFileSync(join(__dirname, "..", "..", "index.html"), "utf8");

function frameSrcTokens(): string[] {
  const policy = indexHtml.match(/<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"/i)?.[1];
  if (!policy) throw new Error("index.html has no Content-Security-Policy meta");
  const directive = policy.split(";").map((part) => part.trim()).find((part) => part.startsWith("frame-src "));
  if (!directive) throw new Error("the Content-Security-Policy meta has no frame-src");
  return directive.slice("frame-src ".length).split(/\s+/);
}

describe("the page's frame-src", () => {
  it("names exactly the sources the frames in the app use", () => {
    // Inventory (apps/desktop/src): two srcdoc iframes, HtmlPreview and HtmlArtboardFrame. Neither
    // has a URL, so `about:` is what admits them; `'self'`, `blob:` and `data:` are the page's own.
    // The third frame is PreviewPane's: the project's own dev server, on this computer, so the two
    // loopback names (never a wildcard host) are what admits it.
    expect(frameSrcTokens()).toEqual([
      "'self'", "about:", "blob:", "data:", "http://localhost:*", "http://127.0.0.1:*",
    ]);
  });

  it("never admits every host or any remote scheme", () => {
    const tokens = frameSrcTokens();
    expect(tokens).not.toContain("*");
    expect(tokens).not.toContain("https:");
    expect(tokens).not.toContain("http:");
    // A source with a host is allowed only when that host is loopback, spelled out.
    expect(
      tokens.filter((token) => /^[a-z]+:\/\//.test(token) && !/^http:\/\/(localhost|127\.0\.0\.1):\*$/.test(token)),
    ).toEqual([]);
  });
});
