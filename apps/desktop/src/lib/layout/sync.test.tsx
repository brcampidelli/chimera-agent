import { act, render, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getUiLayout, putUiLayout } from "@/lib/api";
import { LayoutProvider, useLayout } from "@/lib/layout/context";
import { applyLayout, defaultLayout, type Layout, type LayoutAction } from "@/lib/layout/model";
import { LayoutServerSync } from "@/lib/layout/sync";

vi.mock("@/lib/api", () => ({
  getUiLayout: vi.fn(),
  putUiLayout: vi.fn(async () => ({ layout: null })),
}));

/**
 * Phase 6 of the dynamic screen: the layout is kept on the server as well as in the webview, so a
 * reinstall does not lose it. The first time local goes up; after that the server is the source; a
 * change made before the server answers wins; no server is not an error.
 */
let api: ReturnType<typeof useLayout> | null = null;

function Probe() {
  api = useLayout();
  return null;
}

function mount(initial: Layout) {
  return render(
    <LayoutProvider initial={initial}>
      <LayoutServerSync />
      <Probe />
    </LayoutProvider>,
  );
}

const dispatch = (a: LayoutAction) => act(() => void api?.dispatch(a));

describe("keeping the layout on the server", () => {
  beforeEach(() => {
    localStorage.clear();
    api = null;
    vi.mocked(getUiLayout).mockReset();
    vi.mocked(putUiLayout).mockReset().mockResolvedValue({ layout: null });
  });

  it("applies what the server has at start, and does not send it back", async () => {
    const stored = applyLayout(defaultLayout(), { type: "set-region", region: "rail", visible: false });
    vi.mocked(getUiLayout).mockResolvedValue({ layout: JSON.parse(JSON.stringify(stored)) });

    mount(defaultLayout());

    await waitFor(() => expect(api?.layout.regions.rail.visible).toBe(false));
    await new Promise((r) => setTimeout(r, 700));
    expect(putUiLayout).not.toHaveBeenCalled();
    // Learning what the screen already was is not a step to undo.
    expect(api?.canUndo).toBe(false);
  });

  it("sends the local layout the first time, when the server has none", async () => {
    vi.mocked(getUiLayout).mockResolvedValue({ layout: null });
    const local = applyLayout(defaultLayout(), { type: "resize", region: "left", size: 300 });

    mount(local);

    await waitFor(() => expect(putUiLayout).toHaveBeenCalledTimes(1));
    expect(vi.mocked(putUiLayout).mock.calls[0][0]).toEqual(local);
  });

  it("sends nothing when both are empty", async () => {
    vi.mocked(getUiLayout).mockResolvedValue({ layout: null });

    mount(defaultLayout());

    await waitFor(() => expect(getUiLayout).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 700));
    expect(putUiLayout).not.toHaveBeenCalled();
  });

  it("sends a change once, after a short pause, not once per step", async () => {
    vi.mocked(getUiLayout).mockResolvedValue({ layout: null });
    mount(defaultLayout());
    await waitFor(() => expect(getUiLayout).toHaveBeenCalled());
    await act(async () => void (await Promise.resolve()));

    for (const size of [250, 260, 270, 280]) dispatch({ type: "resize", region: "left", size });

    await waitFor(() => expect(putUiLayout).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect((vi.mocked(putUiLayout).mock.calls[0][0] as Layout).regions.left.size).toBe(280);
  });

  it("keeps a change made before the server answered, and sends it instead", async () => {
    let answer: (v: { layout: Record<string, unknown> | null }) => void = () => undefined;
    vi.mocked(getUiLayout).mockReturnValue(new Promise((r) => (answer = r)));
    mount(defaultLayout());

    dispatch({ type: "set-region", region: "right", visible: false });
    const server = applyLayout(defaultLayout(), { type: "set-region", region: "rail", visible: false });
    await act(async () => answer({ layout: JSON.parse(JSON.stringify(server)) }));

    expect(api?.layout.regions.right.visible).toBe(false);
    expect(api?.layout.regions.rail.visible).toBe(true);
    await waitFor(() => expect(putUiLayout).toHaveBeenCalled());
    expect((vi.mocked(putUiLayout).mock.calls[0][0] as Layout).regions.right.visible).toBe(false);
  });

  it("keeps working on the local copy when there is no server", async () => {
    vi.mocked(getUiLayout).mockRejectedValue(new Error("offline"));
    mount(defaultLayout());
    await waitFor(() => expect(getUiLayout).toHaveBeenCalled());

    dispatch({ type: "set-region", region: "left", visible: false });

    expect(api?.layout.regions.left.visible).toBe(false);
  });

  it("keeps the local layout, and sends it, when what the server has is not a layout it recognises", async () => {
    vi.mocked(getUiLayout).mockResolvedValue({ layout: { version: 99, anything: true } });
    const local = applyLayout(defaultLayout(), { type: "set-region", region: "rail", visible: false });

    mount(local);

    await waitFor(() => expect(putUiLayout).toHaveBeenCalledTimes(1));
    expect(api?.layout.regions.rail.visible).toBe(false);
    expect(vi.mocked(putUiLayout).mock.calls[0][0]).toEqual(local);
  });
});
