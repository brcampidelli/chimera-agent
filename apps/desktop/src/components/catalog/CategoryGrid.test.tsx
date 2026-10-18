/**
 * The category grid inside the catalog shell, against the real hash router: a tile's number is what
 * the screen would list, an empty category has no tile, and pressing one lands in the same `cat=`
 * the filter menu writes.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { CatalogScreen, type CatalogSectionSpec } from "@/components/catalog/CatalogScreen";
import { CATEGORY_IDS } from "@/components/catalog/categories";
import { categoryCounts, COLLAPSED_TILES } from "@/components/catalog/CategoryGrid";
import { item, SERVER } from "@/components/catalog/fixtures";
import { useRoute } from "@/lib/router";
import { renderWithProviders } from "@/test/utils";

const DISCOVER: CatalogSectionSpec[] = [
  {
    id: "all",
    title: "Everything",
    items: [
      item({ id: "a", name: "alpha", category: "dev" }),
      item({ id: "b", name: "beta", category: "software-development" }),
      item({ id: "c", name: "gamma", category: "dev", capabilities: SERVER }),
      item({ id: "d", name: "delta", category: "databases", capabilities: SERVER }),
      item({ id: "e", name: "epsilon", category: "mcp", canonicalCategory: "security" }),
      item({ id: "f", name: "zeta", category: "not-a-category" }),
    ],
  },
];

/** One item in each of the 27, so every tile exists and "Show all" has something to show. */
const EVERY: CatalogSectionSpec[] = [
  {
    id: "every",
    title: "Every category",
    items: CATEGORY_IDS.map((id) => item({ id, name: `item ${id}`, category: id })),
  },
];

function Harness({ discover = DISCOVER, grid }: { discover?: CatalogSectionSpec[]; grid?: boolean }) {
  const { route, setParams } = useRoute();
  return (
    <CatalogScreen
      title="Plugins"
      searchLabel="Search plugins…"
      mine={[{ id: "active", title: "Active", items: [item({ id: "m", name: "mine", status: "active" })] }]}
      discover={discover}
      params={route.params}
      onParamsChange={setParams}
      categoryGrid={grid}
    />
  );
}

function params(): URLSearchParams {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "");
}

function grid() {
  return screen.getByRole("region", { name: "Categories" });
}

function tileNames(): string[] {
  return within(grid())
    .getAllByRole("button")
    .filter((b) => b.hasAttribute("aria-pressed"))
    .map((b) => b.getAttribute("aria-label") ?? "");
}

function cardNames(): string[] {
  return screen.queryAllByRole("article").map((a) => a.getAttribute("aria-label") ?? "");
}

beforeEach(() => {
  window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover");
});

describe("categoryCounts", () => {
  it("counts by the shared id, in grid order, and leaves out what has nothing and what is other", () => {
    expect(categoryCounts(DISCOVER[0].items)).toEqual([
      { id: "code", count: 3 },
      { id: "databases", count: 1 },
      { id: "security", count: 1 },
    ]);
  });
});

describe("CategoryGrid", () => {
  it("draws a tile per category that has items, each with how many", () => {
    renderWithProviders(<Harness />);
    expect(tileNames()).toEqual(["Code, 3 items", "Databases, 1 items", "Security, 1 items"]);
    // The drawings are decoration; the tile's text names the category.
    for (const svg of grid().querySelectorAll("svg")) expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it("counts what is visible: another filter shrinks a tile, and an emptied category disappears", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&runs=network");
    renderWithProviders(<Harness />);
    expect(tileNames()).toEqual(["Code, 1 items", "Databases, 1 items"]);
  });

  it("counts what a search leaves", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    await user.type(screen.getByRole("searchbox", { name: "Search plugins…" }), "delta");
    expect(tileNames()).toEqual(["Databases, 1 items"]);
  });

  it("sets the category filter in the URL, narrows the list, shows the chip, and clears on a second press", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    await user.click(screen.getByRole("button", { name: "Code, 3 items" }));
    expect(params().get("cat")).toBe("code");
    expect(cardNames().sort()).toEqual(["alpha", "beta", "gamma"]);
    expect(screen.getByText("Category: Code")).toBeInTheDocument();
    // The other tiles stay, so the person can move between categories without clearing first.
    expect(tileNames()).toEqual(["Code, 3 items", "Databases, 1 items", "Security, 1 items"]);
    const pressed = screen.getByRole("button", { name: "Code, 3 items" });
    expect(pressed).toHaveAttribute("aria-pressed", "true");

    // The filter menu shows the same box ticked.
    await user.click(screen.getByRole("button", { name: /^Filters/ }));
    expect(await screen.findByRole("menuitemcheckbox", { name: "Code3" })).toHaveAttribute("aria-checked", "true");
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Code, 3 items" }));
    expect(params().has("cat")).toBe(false);
    expect(cardNames()).toHaveLength(6);
  });

  it("reads a category from a pasted link", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&cat=security");
    renderWithProviders(<Harness />);
    expect(cardNames()).toEqual(["epsilon"]);
    expect(screen.getByRole("button", { name: "Security, 1 items" })).toHaveAttribute("aria-pressed", "true");
  });

  it("is operable from the keyboard", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    screen.getByRole("button", { name: "Databases, 1 items" }).focus();
    await user.keyboard("{Enter}");
    expect(params().get("cat")).toBe("databases");
    screen.getByRole("button", { name: "Databases, 1 items" }).focus();
    await user.keyboard(" ");
    expect(params().has("cat")).toBe(false);
  });

  it("collapses to eight tiles, and Show all / Show less toggle the rest with aria-expanded", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness discover={EVERY} />);
    expect(tileNames()).toHaveLength(COLLAPSED_TILES);
    const toggle = within(grid()).getByRole("button", { name: "Show all 27" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(document.getElementById(toggle.getAttribute("aria-controls") ?? "")?.tagName).toBe("UL");

    await user.click(toggle);
    expect(tileNames()).toHaveLength(27);
    const less = within(grid()).getByRole("button", { name: "Show less" });
    expect(less).toHaveAttribute("aria-expanded", "true");

    await user.click(less);
    expect(tileNames()).toHaveLength(COLLAPSED_TILES);
    // Focus stays on the toggle, which is where the person pressed it.
    expect(within(grid()).getByRole("button", { name: "Show all 27" })).toHaveFocus();
  });

  it("never hides the pressed tile behind Show all", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&cat=ai-models-agents");
    renderWithProviders(<Harness discover={EVERY} />);
    expect(tileNames()).toHaveLength(COLLAPSED_TILES + 1);
    expect(screen.getByRole("button", { name: "AI models and agents, 1 items" })).toHaveAttribute("aria-pressed", "true");
  });

  it("is in Discover only, and a screen can opt out", async () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins");
    const { unmount } = renderWithProviders(<Harness />);
    expect(screen.queryByRole("region", { name: "Categories" })).toBeNull();
    unmount();
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover");
    renderWithProviders(<Harness grid={false} />);
    await waitFor(() => expect(screen.getAllByRole("article").length).toBeGreaterThan(0));
    expect(screen.queryByRole("region", { name: "Categories" })).toBeNull();
  });

  it("draws nothing when no item files under any of the 27", () => {
    renderWithProviders(
      <Harness discover={[{ id: "x", title: "Unfiled", items: [item({ id: "u", name: "u", category: "zzz" })] }]} />,
    );
    expect(screen.queryByRole("region", { name: "Categories" })).toBeNull();
  });
});
