import { describe, expect, it } from "vitest";

import { designIdFromSearch, shareLinkFor, withoutDesignParam } from "@/lib/design/deepLink";

describe("the design a link names", () => {
  it("reads ?design=<id>, and ignores anything that is not a plain id", () => {
    expect(designIdFromSearch("?design=doc_1")).toBe("doc_1");
    expect(designIdFromSearch("?design=doc-2&x=1")).toBe("doc-2");
    expect(designIdFromSearch("")).toBeNull();
    expect(designIdFromSearch("?other=doc_1")).toBeNull();
    expect(designIdFromSearch("?design=doc%201")).toBeNull();
    expect(designIdFromSearch("?design=../secrets")).toBeNull();
    expect(designIdFromSearch(`?design=${"a".repeat(97)}`)).toBeNull();
  });

  it("writes a link to the same address, and the link reads back to the same design", () => {
    const link = shareLinkFor("http://localhost:1420/#/code", "doc_1");
    expect(designIdFromSearch(new URL(link).search)).toBe("doc_1");
    expect(new URL(link).hash).toBe("#/code");
  });

  it("takes the parameter off the address again, and leaves the rest alone", () => {
    const link = shareLinkFor("http://localhost:1420/#/code", "doc_1");
    expect(withoutDesignParam(link)).toBe("http://localhost:1420/#/code");
  });
});
