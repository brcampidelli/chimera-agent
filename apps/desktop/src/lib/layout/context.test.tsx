import { act, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LayoutProvider, useLayout } from "@/lib/layout/context";
import { STORAGE_KEY } from "@/lib/layout/store";

/**
 * A window that draws one conversation reads the person's layout and writes none of it.
 *
 * The main window owns the layout. A conversation in a window of its own needs it only to know how
 * the person likes each card drawn; a card preference changed there, saved over the main window's
 * copy, would reach the main screen the next time it loaded, from a place the person was not
 * looking at when they changed it.
 */
function Probe({ onApi }: { onApi: (api: ReturnType<typeof useLayout>) => void }) {
  onApi(useLayout());
  return null;
}

describe("LayoutProvider — a provider that only reads", () => {
  it("does not write the layout, even when it changes", () => {
    localStorage.removeItem(STORAGE_KEY);
    const got: { api: ReturnType<typeof useLayout> | null } = { api: null };
    render(
      <LayoutProvider persist={false}>
        <Probe onApi={(a) => (got.api = a)} />
      </LayoutProvider>,
    );

    act(() => {
      got.api?.dispatch({ type: "card-pref", kind: "receipt", mode: "minimized" });
    });

    expect(got.api?.layout.cards.receipt).toBe("minimized");
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("the ordinary provider still does", () => {
    localStorage.removeItem(STORAGE_KEY);
    const got: { api: ReturnType<typeof useLayout> | null } = { api: null };
    render(
      <LayoutProvider>
        <Probe onApi={(a) => (got.api = a)} />
      </LayoutProvider>,
    );

    act(() => {
      got.api?.dispatch({ type: "card-pref", kind: "receipt", mode: "minimized" });
    });

    expect(localStorage.getItem(STORAGE_KEY)).toContain("minimized");
  });
});
