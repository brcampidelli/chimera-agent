import { describe, expect, it } from "vitest";

import {
  conversationFrom,
  conversationUrl,
  conversationWindowName,
  floatPanelFrom,
  floatUrl,
  floatWindowName,
  isFloatMessage,
} from "@/lib/float/protocol";

/**
 * Phase 7 of the dynamic screen: the address of a panel's own window, and what the two windows accept
 * from each other. The native side opens a window for this one address shape (`is_float_url` in
 * `src-tauri/src/main.rs`), so the two must agree.
 */
describe("the address of a panel's window", () => {
  it("is this origin's root with one parameter, and reads back as the same panel", () => {
    const url = floatUrl("activity.jobs", "http://127.0.0.1:8765");
    expect(url).toBe("http://127.0.0.1:8765/?float=activity.jobs");
    expect(floatPanelFrom(new URL(url).search)).toBe("activity.jobs");
  });

  it("is the app for anything that does not name a panel that can float", () => {
    expect(floatPanelFrom("")).toBeNull();
    expect(floatPanelFrom("?float=")).toBeNull();
    expect(floatPanelFrom("?float=nonsense")).toBeNull();
    // Real panels that never leave their place are not windows either.
    expect(floatPanelFrom("?float=sessions")).toBeNull();
    expect(floatPanelFrom("?float=composer.config")).toBeNull();
  });

  it("names one window per panel, so opening it again focuses the one already open", () => {
    expect(floatWindowName("activity.jobs")).toBe(floatWindowName("activity.jobs"));
    expect(floatWindowName("activity.jobs")).not.toBe(floatWindowName("activity.tools"));
  });
});

describe("what crosses the channel", () => {
  it("takes the four messages, each only with a panel that exists", () => {
    expect(isFloatMessage({ type: "hello", panel: "activity.tools" })).toBe(true);
    expect(isFloatMessage({ type: "closed", panel: "activity.jobs" })).toBe(true);
    expect(isFloatMessage({ type: "return", panel: "activity.machine" })).toBe(true);
    expect(isFloatMessage({ type: "agent", state: { status: "idle", tools: [], report: null, busy: false } })).toBe(true);
  });

  it("refuses anything else", () => {
    expect(isFloatMessage(null)).toBe(false);
    expect(isFloatMessage("hello")).toBe(false);
    expect(isFloatMessage({ type: "hello", panel: "sessions" })).toBe(false);
    expect(isFloatMessage({ type: "closed" })).toBe(false);
    expect(isFloatMessage({ type: "agent", state: { tools: "no" } })).toBe(false);
    expect(isFloatMessage({ type: "navigate", url: "https://example.com" })).toBe(false);
  });
});

/**
 * A conversation in a window of its own (after R16 of the review of 2026-09-30): the same page, asked
 * by `?conversation=<id>` to draw one conversation. The native side opens this shape and no other
 * new one, so the id is held to what a session id can be: letters, digits, `-` and `_`, at most 64.
 */
describe("the address of a conversation's window", () => {
  it("is this origin's root with one parameter, and reads back as the same conversation", () => {
    const url = conversationUrl("3f2a9c", "http://127.0.0.1:8765");
    expect(url).toBe("http://127.0.0.1:8765/?conversation=3f2a9c");
    expect(conversationFrom(new URL(url).search)).toBe("3f2a9c");
  });

  it("is the app for anything that is not a session id", () => {
    expect(conversationFrom("")).toBeNull();
    expect(conversationFrom("?conversation=")).toBeNull();
    expect(conversationFrom("?conversation=../../etc")).toBeNull();
    expect(conversationFrom(`?conversation=${"a".repeat(65)}`)).toBeNull();
    expect(conversationFrom("?float=activity.jobs")).toBeNull();
  });

  it("names one window per conversation, so opening it again focuses the one already open", () => {
    expect(conversationWindowName("abc")).toBe(conversationWindowName("abc"));
    expect(conversationWindowName("abc")).not.toBe(conversationWindowName("abd"));
  });
});
