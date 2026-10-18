/**
 * The catalog's small primitives: the status pill, the search field and its keys, the card's
 * accessible name and description, sections and the skeleton, and the filter and sort menus.
 */
import { useState } from "react";
import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CatalogCard, monogram } from "@/components/catalog/CatalogCard";
import { CatalogSection, CatalogSkeleton } from "@/components/catalog/CatalogSection";
import { FilterChips } from "@/components/catalog/FilterChips";
import { FilterMenu } from "@/components/catalog/FilterMenu";
import { SearchField } from "@/components/catalog/SearchField";
import { SortMenu } from "@/components/catalog/SortMenu";
import { StatusPill } from "@/components/catalog/StatusPill";
import { item, SERVER, UNKNOWN } from "@/components/catalog/fixtures";
import { NO_FILTERS, type CatalogFilters } from "@/components/catalog/model";
import { renderWithProviders } from "@/test/utils";

describe("StatusPill", () => {
  it.each([
    ["pending", "Pending"],
    ["active", "Active"],
    ["held", "Held: changed"],
    ["inactive", "Off"],
    ["denied", "Denied by policy"],
    ["learned", "Learned"],
  ] as const)("names %s in words, never by colour alone", (status, word) => {
    renderWithProviders(<StatusPill status={status} />);
    expect(screen.getByText(word)).toBeInTheDocument();
  });

  it("takes the -foreground ink for its label", () => {
    renderWithProviders(<StatusPill status="denied" />);
    expect(screen.getByText("Denied by policy")).toHaveClass("text-bad-foreground");
  });
});

function Search({ initial = "" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <>
      <input aria-label="elsewhere" />
      <SearchField value={value} onChange={setValue} label="Search plugins…" />
    </>
  );
}

describe("SearchField", () => {
  it("takes focus on / when nobody is typing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Search />);
    await user.keyboard("/");
    const field = screen.getByRole("searchbox", { name: "Search plugins…" });
    expect(field).toHaveFocus();
    // The slash is the shortcut, not the first letter of the query.
    expect(field).toHaveValue("");
  });

  it("leaves / alone while another field is being typed in", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Search />);
    const other = screen.getByRole("textbox", { name: "elsewhere" });
    await user.click(other);
    await user.keyboard("a/b");
    expect(other).toHaveValue("a/b");
    expect(screen.getByRole("searchbox")).not.toHaveFocus();
  });

  it("ignores / with a modifier", () => {
    renderWithProviders(<Search />);
    fireEvent.keyDown(window, { key: "/", ctrlKey: true });
    expect(screen.getByRole("searchbox")).not.toHaveFocus();
  });

  it("clears on the first Escape and leaves on the second", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Search />);
    const field = screen.getByRole("searchbox");
    await user.click(field);
    await user.keyboard("pdf");
    await user.keyboard("{Escape}");
    expect(field).toHaveValue("");
    expect(field).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(field).not.toHaveFocus();
  });

  it("shows the / hint only while the field is idle and empty", async () => {
    const user = userEvent.setup();
    const { container } = renderWithProviders(<Search />);
    expect(container.querySelector("kbd")).not.toBeNull();
    await user.click(screen.getByRole("searchbox"));
    expect(container.querySelector("kbd")).toBeNull();
  });
});

describe("CatalogCard", () => {
  it("is named by its title and described by description, what it runs and its state, in that order", () => {
    renderWithProviders(
      <CatalogCard
        item={item({ name: "Supabase", description: "Queries your project's database.", capabilities: SERVER, status: "pending" })}
      />,
    );
    const card = screen.getByRole("article", { name: "Supabase" });
    expect(card).toHaveAccessibleDescription(
      "Queries your project's database. starts 1 MCP server(s) · reaches the network Pending",
    );
  });

  it("leaves the state out of the description when it is not installed", () => {
    renderWithProviders(<CatalogCard item={item({ name: "notes" })} />);
    expect(screen.getByRole("article", { name: "notes" })).toHaveAccessibleDescription("Does a thing. instructions only");
  });

  it("keeps the capability line the plugin tests read", () => {
    renderWithProviders(<CatalogCard item={item({ capabilities: UNKNOWN })} />);
    expect(screen.getByTestId("capability-line")).toHaveTextContent("known after installing");
  });

  it("installs switched off from its own button, without opening the detail", async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();
    const onInstall = vi.fn();
    renderWithProviders(<CatalogCard item={item({ name: "pdf" })} onOpen={onOpen} onInstall={onInstall} />);
    await user.click(screen.getByRole("button", { name: "Install pdf (arrives switched off)" }));
    expect(onInstall).toHaveBeenCalledTimes(1);
    expect(onOpen).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "pdf" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("keeps an item it cannot install visible, with the reason written on the card", () => {
    renderWithProviders(
      <CatalogCard item={item({ name: "remote", installable: false, refusal: "Remote sources are not fetched." })} onInstall={vi.fn()} />,
    );
    const button = screen.getByRole("button", { name: "remote cannot be installed" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription("Remote sources are not fetched.");
    expect(screen.getByText("Remote sources are not fetched.")).toBeVisible();
  });

  it("shows the state instead of the install button once installed", () => {
    renderWithProviders(<CatalogCard item={item({ name: "pdf", status: "active" })} onInstall={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Install/ })).toBeNull();
    expect(screen.getByText("Active")).toBeInTheDocument();
  });

  it("says when the licence was not read", () => {
    renderWithProviders(<CatalogCard item={item({ license: null })} />);
    expect(screen.getByText("License not read")).toBeInTheDocument();
  });

  it("shows stars with the date they were read, and nothing at all without data", () => {
    const { unmount } = renderWithProviders(<CatalogCard item={item({ stars: 2100, stars_at: "2026-10-12" })} />);
    expect(screen.getByText("★ 2.1K · GitHub, 10/12")).toBeInTheDocument();
    unmount();
    renderWithProviders(<CatalogCard item={item({ stars: null })} />);
    expect(screen.queryByText(/★/)).toBeNull();
    expect(screen.queryByText(/install/i)).toBeNull();
  });

  it("earns the vendor badge as screen-reader text only by rule", () => {
    renderWithProviders(
      <CatalogCard
        item={item({
          name: "Supabase",
          proof: { official: true, vendor_org: "supabase", repo_owner: "supabase", from_anthropic: false, library: false },
        })}
      />,
    );
    expect(screen.getByText("Official vendor")).toHaveClass("sr-only");
  });

  it("draws a monogram, not a logo", () => {
    expect(monogram("Supabase")).toBe("SU");
    expect(monogram("google drive")).toBe("GD");
    expect(monogram("—")).toBe("?");
  });
});

describe("CatalogSection", () => {
  it("is a labelled section with a list and a Show all that names the section", async () => {
    const user = userEvent.setup();
    const onShowAll = vi.fn();
    renderWithProviders(
      <CatalogSection title="New in the catalog" count={9} onShowAll={onShowAll}>
        <li>one</li>
      </CatalogSection>,
    );
    const section = screen.getByRole("region", { name: "New in the catalog" });
    expect(within(section).getByRole("list")).toBeInTheDocument();
    expect(within(section).getByText("9 items")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show all in New in the catalog" }));
    expect(onShowAll).toHaveBeenCalled();
  });

  it("draws the tail under its own label", () => {
    renderWithProviders(
      <CatalogSection title="Results" tail={{ label: "No star data", children: <li>later</li> }}>
        <li>first</li>
      </CatalogSection>,
    );
    expect(within(screen.getByRole("list", { name: "No star data" })).getByText("later")).toBeInTheDocument();
  });

  it("skeleton is busy and says it is loading, without animating", () => {
    const { container } = renderWithProviders(<CatalogSkeleton />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading catalog");
    expect(screen.getByRole("list")).toHaveAttribute("aria-busy", "true");
    expect(container.querySelectorAll("li")).toHaveLength(6);
    expect(container.innerHTML).not.toMatch(/animate-/);
  });
});

function Filters({ initial = NO_FILTERS, onChange }: { initial?: CatalogFilters; onChange?: (f: CatalogFilters) => void }) {
  const [filters, setFilters] = useState(initial);
  const set = (f: CatalogFilters) => {
    setFilters(f);
    onChange?.(f);
  };
  const categories = [{ id: "dev", label: "Development", count: 3 }];
  return (
    <>
      <FilterMenu filters={filters} onChange={set} categories={categories} />
      <FilterChips filters={filters} onChange={set} categories={categories} />
    </>
  );
}

describe("FilterMenu and chips", () => {
  it("ticks a filter, keeps the menu open, and shows it as a removable chip", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<Filters onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: "Filters" }));
    await user.click(await screen.findByRole("menuitemcheckbox", { name: "We don't know yet" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...NO_FILTERS, runs: ["unknown"] });
    expect(screen.getByRole("menuitemcheckbox", { name: "Development3" })).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: "Filters, 1 active" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove filter What it runs: We don't know yet" }));
    expect(onChange).toHaveBeenLastCalledWith(NO_FILTERS);
  });

  it("clears every filter at once", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<Filters initial={{ cat: ["dev"], runs: ["text"], license: ["unread"], state: [] }} onChange={onChange} />);
    expect(screen.getByText("Category: Development")).toBeInTheDocument();
    expect(screen.getByText("License: Not read")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange).toHaveBeenLastCalledWith(NO_FILTERS);
    expect(screen.queryByText("Active filters:")).toBeNull();
  });
});

describe("SortMenu", () => {
  it("names the current order and offers only what it was given", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    renderWithProviders(<SortMenu value="name" options={["name", "stars"]} onChange={onChange} />);
    await user.click(screen.getByRole("button", { name: "Sort: Name" }));
    expect(await screen.findByRole("menuitemradio", { name: "Name" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("menuitemradio", { name: "Most recent" })).toBeNull();
    await user.click(screen.getByRole("menuitemradio", { name: "GitHub stars" }));
    expect(onChange).toHaveBeenCalledWith("stars");
  });
});
