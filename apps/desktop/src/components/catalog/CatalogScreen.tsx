import { useEffect, useId, useMemo, useRef, useState, type ReactNode, type Ref } from "react";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { focusRing } from "@/components/ui/focus";
import { TabPanel, Tabs } from "@/components/ui/tabs";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { CatalogCard } from "@/components/catalog/CatalogCard";
import { CatalogSection, CatalogSkeleton } from "@/components/catalog/CatalogSection";
import { CATEGORY_LABEL_KEY, categoryOfItem, isCategoryId } from "@/components/catalog/categories";
import { CategoryGrid } from "@/components/catalog/CategoryGrid";
import { FilterChips } from "@/components/catalog/FilterChips";
import { FilterMenu } from "@/components/catalog/FilterMenu";
import type { CategoryOption } from "@/components/catalog/filterLabels";
import { SearchField } from "@/components/catalog/SearchField";
import { SortMenu } from "@/components/catalog/SortMenu";
import {
  availableSorts,
  filterCount,
  matchesFilters,
  NO_FILTERS,
  normalize,
  parseFilters,
  parseSort,
  parseView,
  relevance,
  sortItems,
  type CatalogFilters,
  type CatalogItem,
  type CatalogView,
  type SortValue,
} from "@/components/catalog/model";

export interface CatalogSectionSpec {
  id: string;
  title: string;
  items: readonly CatalogItem[];
  /** Cards drawn before "Show all". Omit to draw every one. */
  limit?: number;
  /** The caller's order means something ("most stars"): keep it unless the person picks a sort. */
  ordered?: boolean;
  /** "For you" only: the local reason per item id. */
  why?: Readonly<Record<string, string>>;
}

/** Words a kind may say in its own voice instead of the shell's neutral ones. */
export interface CatalogLabels {
  mine?: string;
  discover?: string;
  /** The empty Mine title: "No plugins installed." rather than "Nothing here yet". */
  emptyMine?: string;
  noMatchFilters?: string;
}

/** How long the URL waits before taking the query. The list itself filters on every keystroke. */
const URL_DEBOUNCE_MS = 120;
/** How long the result count waits before it is announced — a count per keystroke is noise. */
const ANNOUNCE_DEBOUNCE_MS = 500;

/** Each item once, in section order: six cards in a section should be six choices, not repeats of
 *  the section above. */
function dedupe(sections: readonly CatalogSectionSpec[]): CatalogSectionSpec[] {
  const seen = new Set<string>();
  return sections.map((s) => ({
    ...s,
    items: s.items.filter((i) => {
      if (seen.has(i.id)) return false;
      seen.add(i.id);
      return true;
    }),
  }));
}

/**
 * The menu's category options: the raw labels the items carry, as before, plus any of the 27 shared
 * ids a category tile put in `cat=` — so the menu shows that box ticked and the chip has a name.
 */
function categoriesOf(
  items: readonly CatalogItem[],
  label: (id: string) => string,
  selected: readonly string[],
): CategoryOption[] {
  const counts = new Map<string, number>();
  for (const i of items) counts.set(i.category, (counts.get(i.category) ?? 0) + 1);
  for (const id of selected) {
    if (counts.has(id) || !isCategoryId(id)) continue;
    counts.set(id, items.filter((i) => categoryOfItem(i) === id).length);
  }
  return [...counts]
    .map(([id, count]) => ({ id, label: label(id), count }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * The shell every catalog screen shares (study 04, §2): title, Mine | Discover, search, filters,
 * sort, then sections of cards. Presentation only — the caller hands it items already shaped by
 * `model.ts` and decides what opening or installing does.
 *
 * Everything a person chooses here lives in the URL (`view`, `q`, `cat`, `runs`, `license`,
 * `state`, `sort`, `section`), through `params` / `onParamsChange`, so it survives a reload and a
 * pasted link. Any value the screen does not know falls back to the default; none empties it.
 */
export function CatalogScreen({
  title,
  subtitle,
  searchLabel,
  mine,
  discover,
  params,
  onParamsChange,
  categoryLabel,
  addMenu,
  loading = false,
  mineEmptyText,
  onOpen,
  onInstall,
  installingId = null,
  headingRef,
  labels = {},
  addMenuInEmpty = true,
  installLabel,
  renderAction,
  renderNote,
  discoverHeader,
  discoverFooter,
  notice,
  mineHeader,
  categoryGrid = true,
}: {
  title: string;
  subtitle?: string;
  /** Placeholder and accessible name of the search: "Search plugins…". */
  searchLabel: string;
  mine: readonly CatalogSectionSpec[];
  discover: readonly CatalogSectionSpec[];
  params: URLSearchParams;
  /** Receives the WHOLE query, other screens' keys included — pass it to a replacing `setParams`. */
  onParamsChange: (next: Record<string, string>) => void;
  categoryLabel?: (id: string) => string;
  /** The "+ Add" menu, drawn at the right of the title and in the empty state. */
  addMenu?: ReactNode;
  loading?: boolean;
  mineEmptyText?: string;
  onOpen?: (item: CatalogItem) => void;
  onInstall?: (item: CatalogItem) => void;
  installingId?: string | null;
  headingRef?: Ref<HTMLHeadingElement>;
  labels?: CatalogLabels;
  /** Repeat `addMenu` in the empty Mine state. Off for a menu that owns dialogs, which mounting
   *  twice would mount twice — and two buttons with one name on one screen. */
  addMenuInEmpty?: boolean;
  installLabel?: (item: CatalogItem) => string;
  /** A kind's own control beside a card's state (a plugin's switch). */
  renderAction?: (item: CatalogItem) => ReactNode;
  /** A kind's own line under a card's meta. */
  renderNote?: (item: CatalogItem) => ReactNode;
  /** Above Discover's sections: what the catalog is and is not. It also stands in for an empty
   *  Discover, which must say why it is empty rather than "nothing matches these filters". */
  discoverHeader?: ReactNode;
  /** Below Discover's sections: where the items come from (a plugin's marketplaces, last). */
  discoverFooter?: ReactNode;
  /** One line under the header: "This item is no longer in the catalog." */
  notice?: ReactNode;
  /** Above Mine's sections: what a kind needs said before its list (a skill's "active now"). Drawn
   *  in the empty state too, because an empty list does not make it less true. */
  mineHeader?: ReactNode;
  /** The category grid at the foot of Discover (study 04, §4). On unless a screen opts out. */
  categoryGrid?: boolean;
}) {
  const { t, lang } = useI18n();
  const tabsId = useId();
  const view = parseView(params);
  const filters = parseFilters(params);
  const sortParam = parseSort(params);
  const sectionParam = params.get("section");

  // Read at call time, so a debounced write cannot put back a filter removed in between.
  const latest = useRef(params);
  latest.current = params;
  function update(patch: Record<string, string | null>) {
    const next = new URLSearchParams(latest.current);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, value);
    }
    latest.current = next;
    onParamsChange(Object.fromEntries(next));
  }

  const [query, setQuery] = useState(() => params.get("q") ?? "");
  const urlQuery = params.get("q") ?? "";
  useEffect(() => {
    if (query === urlQuery) return;
    const timer = window.setTimeout(() => update({ q: query }), URL_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
    // `update` reads the latest params by ref; only the query should restart the wait.
  }, [query]);

  const setFilters = (next: CatalogFilters) =>
    update({
      cat: next.cat.join(","),
      runs: next.runs.join(","),
      license: next.license.join(","),
      state: next.state.join(","),
    });

  const mineSections = useMemo(() => dedupe(mine), [mine]);
  const discoverSections = useMemo(() => dedupe(discover), [discover]);
  const sections = view === "mine" ? mineSections : discoverSections;
  const allItems = useMemo(() => sections.flatMap((s) => s.items), [sections]);
  const mineCount = mineSections.reduce((n, s) => n + s.items.length, 0);
  // The caller words its own labels; one of the 27 shared ids it leaves as-is gets the app's name.
  const labelOf = (id: string) => {
    const own = categoryLabel ? categoryLabel(id) : id;
    return own === id && isCategoryId(id) ? t(CATEGORY_LABEL_KEY[id]) : own;
  };
  const categories = categoriesOf(allItems, labelOf, filters.cat);

  const searching = normalize(query) !== "";
  const sortOptions = availableSorts(allItems, searching);
  const sort: SortValue = sortParam && sortOptions.includes(sortParam) ? sortParam : searching ? "relevance" : "name";
  const nFilters = filterCount(filters);
  const visible = (items: readonly CatalogItem[]) =>
    items.filter((i) => matchesFilters(i, filters) && (!searching || relevance(i, query) !== null));
  const totalVisible = visible(allItems).length;
  // What each tile counts: everything chosen except the category itself, so a tile's number is
  // what pressing it would list, and the other tiles do not vanish once one is pressed.
  const facet: CatalogFilters = { ...filters, cat: [] };
  const gridItems = allItems.filter((i) => matchesFilters(i, facet) && (!searching || relevance(i, query) !== null));
  const rootRef = useRef<HTMLDivElement>(null);
  const pickCategory = (id: string | null) => {
    update({ cat: id, section: null });
    // Back to the top, where the filtered list starts. `scrollIntoView` with no behaviour jumps, so
    // there is nothing for reduced motion to tone down.
    rootRef.current?.scrollIntoView?.({ block: "start" });
  };

  // One status region for the screen. It announces counts, never card text.
  const [announcement, setAnnouncement] = useState("");
  const narrowing = searching || nFilters > 0;
  useEffect(() => {
    const timer = window.setTimeout(
      () => setAnnouncement(narrowing ? t("catalogUi.results", { n: totalVisible }) : ""),
      ANNOUNCE_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [narrowing, totalVisible, t]);

  // Into or out of a section's full view: focus follows, so a keyboard user is not left at the top.
  const sectionHeading = useRef<HTMLHeadingElement>(null);
  const firstRender = useRef(true);
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    sectionHeading.current?.focus();
  }, [sectionParam]);

  const card = (item: CatalogItem, why?: string) => (
    <li key={item.id}>
      <CatalogCard
        item={item}
        onOpen={onOpen}
        onInstall={onInstall}
        installing={installingId === item.id}
        why={why}
        installLabel={installLabel?.(item)}
        action={renderAction?.(item)}
        note={renderNote?.(item)}
      />
    </li>
  );

  function renderSection(spec: CatalogSectionSpec, full: boolean) {
    const items = visible(spec.items);
    if (items.length === 0) return null;
    const keepOrder = spec.ordered && !sortParam && !searching;
    const sorted = keepOrder ? { ranked: items, unranked: [] } : sortItems(items, sort, lang, query);
    const combined = [...sorted.ranked, ...sorted.unranked];
    const limit = full || spec.limit === undefined ? combined.length : spec.limit;
    const shown = combined.slice(0, limit);
    const rankedIds = new Set(sorted.ranked.map((i) => i.id));
    const head = shown.filter((i) => rankedIds.has(i.id));
    const rest = shown.filter((i) => !rankedIds.has(i.id));
    const tailLabel = sort === "stars" ? t("catalogUi.noStarData") : t("catalogUi.noDateData");
    return (
      <CatalogSection
        key={spec.id}
        ref={full ? sectionHeading : undefined}
        title={spec.title}
        count={items.length}
        onShowAll={!full && combined.length > limit ? () => update({ section: spec.id }) : undefined}
        tail={rest.length > 0 ? { label: tailLabel, children: rest.map((i) => card(i, spec.why?.[i.id])) } : null}
      >
        {head.map((i) => card(i, spec.why?.[i.id]))}
      </CatalogSection>
    );
  }

  function body(): ReactNode {
    if (loading) return <CatalogSkeleton />;
    if (view === "mine" && mineCount === 0) {
      return (
        <div className="space-y-3 py-6 text-center">
          <p className="text-xl font-semibold">{labels.emptyMine ?? t("catalogUi.emptyMine")}</p>
          {mineEmptyText && <p className="text-sm text-muted-foreground">{mineEmptyText}</p>}
          <div className="flex justify-center gap-2">
            <Button size="sm" variant="outline" onClick={() => update({ view: "discover" })}>
              {labels.discover ?? t("catalogUi.view.discover")}
            </Button>
            {addMenuInEmpty && addMenu}
          </div>
        </div>
      );
    }
    // Nothing listed and nothing narrowing it: the header says why. "Nothing matches these
    // filters" with no filter set would blame the person for an empty catalog.
    if (allItems.length === 0 && !narrowing) return null;
    if (totalVisible === 0) {
      return (
        <div className="space-y-3 py-6 text-center text-sm">
          <p>
            {searching
              ? t("catalogUi.noMatch", { q: query.trim() })
              : (labels.noMatchFilters ?? t("catalogUi.noMatchFilters"))}
          </p>
          <div className="flex justify-center gap-2">
            {searching && (
              <Button size="sm" variant="outline" onClick={() => setQuery("")}>
                {t("catalogUi.clearSearch")}
              </Button>
            )}
            {nFilters > 0 && (
              <Button size="sm" variant="outline" onClick={() => setFilters(NO_FILTERS)}>
                {t("catalogUi.removeFilters", { n: nFilters })}
              </Button>
            )}
          </div>
        </div>
      );
    }
    if (searching) {
      // With a query there are no sections: a section would bury a hit under "Show all".
      const results: CatalogSectionSpec = {
        id: "results",
        title: t("catalogUi.resultsFor", { n: totalVisible, q: query.trim() }),
        items: allItems,
      };
      return renderSection(results, true);
    }
    const focused = sections.find((s) => s.id === sectionParam);
    if (focused) {
      return (
        <div className="space-y-3">
          <button
            type="button"
            onClick={() => update({ section: null })}
            className={cn("inline-flex items-center gap-1 rounded-md text-xs text-accent-ink hover:underline", focusRing)}
          >
            <ArrowLeft aria-hidden className="h-3 w-3" />
            {view === "mine"
              ? (labels.mine ?? t("catalogUi.view.mine"))
              : (labels.discover ?? t("catalogUi.view.discover"))}
          </button>
          {renderSection(focused, true)}
        </div>
      );
    }
    return <div className="space-y-8">{sections.map((s) => renderSection(s, false))}</div>;
  }

  return (
    <div ref={rootRef} className="@container space-y-6">
      <header className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 space-y-0.5">
            <h2 ref={headingRef} tabIndex={-1} className="text-lg font-semibold outline-hidden">
              {title}
            </h2>
            {subtitle && <p className="text-xs text-muted-foreground">{subtitle}</p>}
          </div>
          {addMenu}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Tabs<CatalogView>
            id={tabsId}
            variant="segmented"
            aria-label={t("catalogUi.view.label")}
            items={[
              {
                value: "mine",
                label: labels.mine ?? t("catalogUi.view.mine"),
                count: mineCount > 0 ? mineCount : undefined,
              },
              { value: "discover", label: labels.discover ?? t("catalogUi.view.discover") },
            ]}
            value={view}
            onChange={(next) => update({ view: next === "mine" ? null : next, section: null })}
          />
          <SearchField
            value={query}
            onChange={setQuery}
            label={searchLabel}
            className="field order-last flex h-8 w-full min-w-0 items-center gap-2 px-2 @xl:order-none @xl:w-auto @xl:flex-1"
          />
          <div className="ml-auto flex items-center gap-1 @xl:ml-0">
            <FilterMenu filters={filters} onChange={setFilters} categories={categories} />
            <SortMenu value={sort} options={sortOptions} onChange={(next) => update({ sort: next })} />
          </div>
        </div>
        <FilterChips filters={filters} onChange={setFilters} categories={categories} />
        {notice}
      </header>
      <p role="status" className="sr-only">
        {announcement}
      </p>
      <TabPanel tabsId={tabsId} value={view}>
        {view === "mine" && mineHeader ? <div className="mb-6">{mineHeader}</div> : null}
        {view === "discover" && discoverHeader ? <div className="mb-6">{discoverHeader}</div> : null}
        {body()}
        {view === "discover" && categoryGrid && !loading && !sections.some((s) => s.id === sectionParam) ? (
          <CategoryGrid className="mt-8" items={gridItems} active={filters.cat} onSelect={pickCategory} />
        ) : null}
        {view === "discover" && discoverFooter ? <div className="mt-8">{discoverFooter}</div> : null}
      </TabPanel>
    </div>
  );
}
