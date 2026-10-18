/**
 * The catalog shell against the real hash router: what a person chooses has to land in the URL and
 * come back out of it, because that is what lets a link or a reload show the same screen.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { CatalogScreen, type CatalogSectionSpec } from "@/components/catalog/CatalogScreen";
import { item, SERVER, UNKNOWN } from "@/components/catalog/fixtures";
import { useRoute } from "@/lib/router";
import { renderWithProviders } from "@/test/utils";

const MINE: CatalogSectionSpec[] = [
  { id: "active", title: "Active", items: [item({ id: "a", name: "alpha", status: "active" })] },
];

const DISCOVER: CatalogSectionSpec[] = [
  {
    id: "works",
    title: "Work without setup",
    limit: 2,
    items: [
      item({ id: "notes", name: "notes", stars: 0 }),
      item({ id: "pdf", name: "pdf tools", stars: 2100, stars_at: "2026-10-12" }),
      item({ id: "mystery", name: "mystery", capabilities: UNKNOWN }),
    ],
  },
  {
    id: "servers",
    title: "Servers",
    items: [
      item({ id: "db", name: "database", capabilities: SERVER, category: "databases" }),
      // Already in the section above: drawn once, there.
      item({ id: "notes", name: "notes", stars: 0 }),
    ],
  },
];

function Harness({ mine = MINE, discover = DISCOVER }: { mine?: CatalogSectionSpec[]; discover?: CatalogSectionSpec[] }) {
  const { route, setParams } = useRoute();
  return (
    <CatalogScreen
      title="Plugins"
      subtitle="Claude Code packages."
      searchLabel="Search plugins…"
      mine={mine}
      discover={discover}
      params={route.params}
      onParamsChange={setParams}
      categoryLabel={(id) => ({ dev: "Development", databases: "Databases" })[id] ?? id}
    />
  );
}

function params(): URLSearchParams {
  return new URLSearchParams(window.location.hash.split("?")[1] ?? "");
}

function cardNames(container: HTMLElement): string[] {
  return within(container)
    .queryAllByRole("article")
    .map((a) => a.getAttribute("aria-label") ?? "");
}

beforeEach(() => {
  window.history.replaceState(null, "", "#/settings?tab=plugins");
});

describe("CatalogScreen", () => {
  it("switches Mine | Discover through the URL and keeps the screen's own keys", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    expect(screen.getByRole("heading", { level: 2, name: "Plugins" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Mine1" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Discover" }));
    expect(params().get("view")).toBe("discover");
    expect(params().get("tab")).toBe("plugins");
    await user.click(screen.getByRole("tab", { name: "Mine1" }));
    expect(params().has("view")).toBe(false);
  });

  it("draws each item once, in the first section that has it, and cuts a section at its limit", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover");
    renderWithProviders(<Harness />);
    const works = screen.getByRole("region", { name: "Work without setup" });
    expect(cardNames(works)).toEqual(["mystery", "notes"]);
    expect(within(works).getByText("3 items")).toBeInTheDocument();
    expect(cardNames(screen.getByRole("region", { name: "Servers" }))).toEqual(["database"]);
  });

  it("opens a section's full view from Show all, with focus on its title, and comes back", async () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover");
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    await user.click(screen.getByRole("button", { name: "Show all in Work without setup" }));
    expect(params().get("section")).toBe("works");
    const heading = screen.getByRole("heading", { name: "Work without setup" });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(cardNames(screen.getByRole("region", { name: "Work without setup" }))).toHaveLength(3);
    expect(screen.queryByRole("region", { name: "Servers" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Discover" }));
    expect(params().has("section")).toBe(false);
  });

  it("puts filters in the URL, and reads them back on load", async () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover");
    const user = userEvent.setup();
    const { unmount } = renderWithProviders(<Harness />);
    await user.click(screen.getByRole("button", { name: "Filters" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "Reaches the network" }));
    await waitFor(() => expect(params().get("runs")).toBe("network"));
    unmount();

    renderWithProviders(<Harness />);
    expect(screen.getByText("What it runs: Reaches the network")).toBeInTheDocument();
    expect(screen.getAllByRole("article").map((a) => a.getAttribute("aria-label"))).toEqual(["database"]);
  });

  it("shows only the unknown under 'we don't know yet', and never under another filter", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&runs=unknown");
    const { unmount } = renderWithProviders(<Harness />);
    expect(screen.getAllByRole("article").map((a) => a.getAttribute("aria-label"))).toEqual(["mystery"]);
    unmount();
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&runs=text");
    renderWithProviders(<Harness />);
    expect(screen.queryByRole("article", { name: "mystery" })).toBeNull();
  });

  it("sorts by stars with the items that have none at the end, under their own label", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&section=works&sort=stars");
    renderWithProviders(<Harness />);
    const section = screen.getByRole("region", { name: "Work without setup" });
    // `notes` has 0 stars — data — so it ranks; `mystery` has none, so it goes after the label.
    const tail = within(section).getByRole("list", { name: "No star data" });
    expect(cardNames(tail)).toEqual(["mystery"]);
    const all = cardNames(section);
    expect(all).toEqual(["pdf tools", "notes", "mystery"]);
  });

  it("falls back to the default order when the URL asks for a sort the data cannot answer", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&sort=stars");
    renderWithProviders(<Harness />);
    expect(screen.getByRole("button", { name: "Sort: Name" })).toBeInTheDocument();
  });

  it("searches as you type, writes q to the URL after a pause, and drops the sections", async () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover");
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    await user.type(screen.getByRole("searchbox", { name: "Search plugins…" }), "pdf");
    expect(screen.getByRole("heading", { name: "1 results for “pdf”" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Servers" })).toBeNull();
    await waitFor(() => expect(params().get("q")).toBe("pdf"));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("1 results"));
  });

  it("offers a way out of a search or filters that match nothing", async () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover&q=zzz&license=other");
    const user = userEvent.setup();
    renderWithProviders(<Harness />);
    expect(screen.getByText("Nothing for “zzz”.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove filters (1)" }));
    expect(params().has("license")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Clear search" }));
    await waitFor(() => expect(params().has("q")).toBe(false));
    expect(screen.getAllByRole("article").length).toBeGreaterThan(0);
  });

  it("says plainly when Mine is empty and points to Discover", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness mine={[]} />);
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Mine" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discover" }));
    expect(params().get("view")).toBe("discover");
  });

  it("never shows an install count", () => {
    window.history.replaceState(null, "", "#/settings?tab=plugins&view=discover");
    const { container } = renderWithProviders(<Harness />);
    expect(container.textContent ?? "").not.toMatch(/install(s|ed by)|download|users/i);
  });
});
