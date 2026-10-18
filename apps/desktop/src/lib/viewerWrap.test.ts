import { beforeEach, describe, expect, it } from "vitest";

import { VIEWER_WRAP_KEY, readViewerWrap, writeViewerWrap } from "@/lib/viewerWrap";

describe("the file viewer's line wrapping", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to wrapped for prose and whole lines for code", () => {
    expect(readViewerWrap("prose")).toBe(true);
    expect(readViewerWrap("code")).toBe(false);
  });

  it("remembers each kind's choice on its own", () => {
    writeViewerWrap("code", true);
    expect(readViewerWrap("code")).toBe(true);
    expect(readViewerWrap("prose")).toBe(true);
    writeViewerWrap("prose", false);
    expect(readViewerWrap("prose")).toBe(false);
    expect(readViewerWrap("code")).toBe(true);
  });

  it("reads a damaged value as the defaults", () => {
    localStorage.setItem(VIEWER_WRAP_KEY, "not json");
    expect(readViewerWrap("code")).toBe(false);
    localStorage.setItem(VIEWER_WRAP_KEY, JSON.stringify({ code: "yes" }));
    expect(readViewerWrap("code")).toBe(false);
  });
});
