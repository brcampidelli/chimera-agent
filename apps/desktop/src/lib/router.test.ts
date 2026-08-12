import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { DEFAULT_VIEW, formatHash, parseHash, useRoute } from "@/lib/router";

/**
 * The router is two pure functions and a hook over `hashchange`.
 *
 * The pure half carries the judgement — what counts as a screen, what a stale URL falls back to —
 * so it is tested directly and without a DOM. The hook is tested for the three things that are
 * genuinely about the browser: reading the initial hash, following the back button, and not filling
 * history with steps nobody wants to retrace.
 */

afterEach(() => {
  window.location.hash = "";
});

describe("parseHash", () => {
  it("reads a bare screen", () => {
    expect(parseHash("#/work").view).toBe("work");
  });

  it("tolerates the shapes a URL actually arrives in", () => {
    // Vite, Tauri and a hand-typed address bar do not agree on the leading characters.
    expect(parseHash("#work").view).toBe("work");
    expect(parseHash("work").view).toBe("work");
  });

  it("falls back to the default rather than rendering nothing", () => {
    // A stale bookmark, a renamed screen, a typo. Landing somewhere real beats a blank app.
    expect(parseHash("#/does-not-exist").view).toBe(DEFAULT_VIEW);
    expect(parseHash("").view).toBe(DEFAULT_VIEW);
    expect(parseHash("#/").view).toBe(DEFAULT_VIEW);
  });

  it("carries params", () => {
    const route = parseHash("#/code?file=src%2Fapi.ts&line=42");
    expect(route.view).toBe("code");
    expect(route.params.get("file")).toBe("src/api.ts");
    expect(route.params.get("line")).toBe("42");
  });

  it("gives an empty params bag rather than null when there are none", () => {
    // So callers can read `.get()` without a guard at every site.
    expect([...parseHash("#/work").params.keys()]).toEqual([]);
  });

  it("refuses a dev-only screen in a shipped build", () => {
    // Maturity needs a source checkout. Parsing it in a release would land on a blank screen, which
    // reads as a broken app rather than a link that no longer applies.
    expect(parseHash("#/maturity", true).view).toBe("maturity");
    expect(parseHash("#/maturity", false).view).toBe(DEFAULT_VIEW);
  });
});

describe("formatHash", () => {
  it("keeps the common URL readable", () => {
    expect(formatHash("code")).toBe("#/code");
    expect(formatHash("code", {})).toBe("#/code");
  });

  it("encodes params", () => {
    expect(formatHash("code", { file: "src/api.ts" })).toBe("#/code?file=src%2Fapi.ts");
  });

  it("round-trips", () => {
    const params = { file: "a b/c.ts", line: "7" };
    const back = parseHash(formatHash("work", params));
    expect(back.view).toBe("work");
    expect(back.params.get("file")).toBe("a b/c.ts");
    expect(back.params.get("line")).toBe("7");
  });
});

describe("useRoute", () => {
  it("starts from the URL, not from a default", () => {
    window.location.hash = "#/knowledge";
    const { result } = renderHook(() => useRoute());
    expect(result.current.view).toBe("knowledge");
  });

  it("follows the back button", () => {
    // The whole reason for a router: history is the browser's job, not a stack we maintain.
    const { result } = renderHook(() => useRoute());

    act(() => result.current.navigate("automation"));
    expect(result.current.view).toBe("automation");

    act(() => {
      window.location.hash = "#/work";
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
    expect(result.current.view).toBe("work");
  });

  it("navigate writes the address", () => {
    const { result } = renderHook(() => useRoute());
    act(() => result.current.navigate("code", { file: "x.ts" }));
    expect(window.location.hash).toBe("#/code?file=x.ts");
    expect(result.current.route.params.get("file")).toBe("x.ts");
  });

  it("setParams stays on the screen and does not push history", () => {
    // A cursor move or a tab switch changes params constantly. Pushing each one would make the back
    // button walk through every keystroke instead of returning to the previous screen.
    window.location.hash = "#/code";
    const { result } = renderHook(() => useRoute());
    const before = window.history.length;

    act(() => result.current.setParams({ file: "y.ts" }));

    expect(result.current.view).toBe("code");
    expect(result.current.route.params.get("file")).toBe("y.ts");
    expect(window.history.length).toBe(before);
  });
});
