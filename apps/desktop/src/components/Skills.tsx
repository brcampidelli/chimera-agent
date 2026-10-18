import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getPinnedCatalog,
  getSkillBundles,
  getSkillCatalog,
  getSkillLibrary,
  getSkills,
  importSkillLibraryCard,
  installSkillBundle,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { useT, type TFunc } from "@/lib/i18n";
import { useRoute } from "@/lib/router";
import { CatalogScreen } from "@/components/catalog/CatalogScreen";
import type { CatalogItem } from "@/components/catalog/model";
import { AddSkillMenu } from "@/components/skills/AddSkillMenu";
import { SkillCardNote } from "@/components/skills/SkillCardNote";
import { isUnread, SkillDetail } from "@/components/skills/SkillDetail";
import { BundleSwitch, errorText, LearnedActions } from "@/components/skills/SkillParts";
import { DiscoverHeader, MineHeader } from "@/components/skills/SkillsHeaders";
import {
  discoverSections,
  fromBundle,
  fromEntry,
  fromLearned,
  fromLibrary,
  fromPinned,
  LEARNED_CATEGORY,
  mineSections,
  RATINGS,
  UPLOAD_CATEGORY,
  type Rating,
  type SkillSources,
} from "@/components/skills/skillItems";
import { targetsOf, type SkillTarget } from "@/components/skills/targets";

/** The card an id stands for, built from its record whichever tab drew it. */
function itemOf(id: string, target: SkillTarget, t: TFunc): CatalogItem {
  switch (target.kind) {
    case "learned":
      return fromLearned(target.stat, t);
    case "library":
      return fromLibrary(target.card);
    case "entry":
      return id.startsWith("skill:bundle:") && target.bundle
        ? fromBundle(target.bundle, target.entry)
        : fromEntry(target.entry, target.bundle);
    case "bundle":
      return fromBundle(target.bundle);
    case "pinned":
      return fromPinned(target.item, t);
  }
}

/**
 * Settings › Personalization › Skills, on the shared catalog shell (study 04, PR 5).
 *
 * Mine opens with what a run started now is told, then what waits for a decision, what is on, what
 * is off, and the cards the agent learned with their uses and wins. Discover holds the library that
 * ships in the box, the pinned public catalogue and the third-party catalogue, each card carrying
 * its verdict on whether it works here and its licence. Two promises the screen keeps: nothing
 * someone else wrote is switched on by installing it, and no number here is invented.
 *
 * The detail panel lives in the URL as `item=`; opening it pushes a history entry so Back closes it,
 * exactly as Plugins does.
 */
// `embedded` is accepted for the screens that still mount this one inside their own frame.
export function Skills(_props: { embedded?: boolean } = {}) {
  const t = useT();
  const client = useQueryClient();
  const { route, navigate, setParams } = useRoute();
  const params = route.params;
  const skills = useQuery({ queryKey: ["skills"], queryFn: getSkills });
  const bundles = useQuery({ queryKey: ["skill-bundles"], queryFn: getSkillBundles });
  const library = useQuery({ queryKey: ["skill-library"], queryFn: getSkillLibrary });
  const catalog = useQuery({ queryKey: ["skill-catalog"], queryFn: getSkillCatalog });
  const pinned = useQuery({ queryKey: ["pinned-catalog", "skills"], queryFn: () => getPinnedCatalog("skills") });

  // Names whose SKILL.md was on the screen in this session. Not persisted: consent to a text is
  // given by reading it now, not by having read some version once.
  const [read, setRead] = useState<ReadonlySet<string>>(() => new Set());
  const markRead = useCallback(
    (name: string) => setRead((prev) => (prev.has(name) ? prev : new Set(prev).add(name))),
    [],
  );

  const fitParam = params.get("fit");
  const fit = (RATINGS as readonly string[]).includes(fitParam ?? "") ? (fitParam as Rating) : null;
  const grouping = params.get("group") === "name" ? "name" : "topic";
  const src: SkillSources = useMemo(
    () => ({
      stats: skills.data?.stats ?? [],
      bundles: bundles.data ?? [],
      library: library.data ?? [],
      entries: catalog.data ?? [],
      pinned: pinned.data,
    }),
    [skills.data, bundles.data, library.data, catalog.data, pinned.data],
  );
  const candidates = useMemo(() => new Set(skills.data?.retirement_candidates ?? []), [skills.data]);
  const targets = useMemo(() => targetsOf(src, candidates), [src, candidates]);
  const mine = useMemo(() => mineSections(src, t), [src, t]);
  const discover = useMemo(() => discoverSections(src, { grouping, fit }, t), [src, grouping, fit, t]);

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ["skill-catalog"] });
    void client.invalidateQueries({ queryKey: ["skill-bundles"] });
    void client.invalidateQueries({ queryKey: ["skills-effective"] });
    // A library card moves into the learned store, and its row must stop offering the import.
    void client.invalidateQueries({ queryKey: ["skills"] });
    void client.invalidateQueries({ queryKey: ["skill-library"] });
  };
  // Installing lands the skill switched off; importing a library card puts it in the learned store.
  // Neither switches anything on — that stays a separate click on a separate control.
  const install = useMutation({
    mutationFn: async ({ target }: { item: CatalogItem; target: SkillTarget }): Promise<void> => {
      if (target.kind === "library") await importSkillLibraryCard(target.card.name);
      else if (target.kind === "entry") await installSkillBundle(target.entry.name ?? "");
    },
    onSuccess: refresh,
  });
  const onInstall = (item: CatalogItem) => {
    const target = targets.get(item.id);
    if (!target || !item.installable) return;
    if (target.kind === "library" || (target.kind === "entry" && !target.entry.installed)) install.mutate({ item, target });
  };
  const installingId = install.isPending && install.variables ? install.variables.item.id : null;
  const installError = install.error && install.variables ? { id: install.variables.item.id, text: errorText(install.error) } : null;

  const itemId = params.get("item");
  const target = itemId ? (targets.get(itemId) ?? null) : null;
  const detailItem = itemId && target ? itemOf(itemId, target, t) : null;
  const loaded = !skills.isLoading && !bundles.isLoading && !library.isLoading && !catalog.isLoading && !pinned.isLoading;
  const missing = itemId !== null && loaded && target === null;

  const pushed = useRef(false);
  useEffect(() => {
    if (!itemId) pushed.current = false;
  }, [itemId]);
  const withPatch = (patch: Record<string, string | null>) => {
    const next = Object.fromEntries(params);
    for (const [key, value] of Object.entries(patch)) {
      if (value === null) delete next[key];
      else next[key] = value;
    }
    return next;
  };
  const open = (item: CatalogItem) => {
    navigate(route.view, { ...Object.fromEntries(params), item: item.id });
    pushed.current = true;
  };
  const close = () => {
    if (pushed.current) {
      pushed.current = false;
      window.history.back();
    } else {
      setParams(withPatch({ item: null }));
    }
  };

  const view = params.get("view") === "discover" ? "discover" : "mine";
  const learnedEmpty = (skills.data?.stats ?? []).length === 0;

  return (
    <>
      <CatalogScreen
        title={t("skills.title")}
        subtitle={t("skills.subtitle")}
        searchLabel={t("catalog.search")}
        mine={mine}
        discover={discover}
        params={params}
        onParamsChange={setParams}
        categoryLabel={(id) => (id === LEARNED_CATEGORY ? t("skills.learned") : id === UPLOAD_CATEGORY ? t("skills.upload.title") : id)}
        addMenu={<AddSkillMenu onLibrary={() => setParams(withPatch({ view: "discover", section: "chimera", item: null }))} />}
        addMenuInEmpty={false}
        loading={view === "mine" ? skills.isLoading || bundles.isLoading : library.isLoading || catalog.isLoading}
        labels={{ emptyMine: t("skills.empty.mine") }}
        mineEmptyText={`${t("skills.empty")} ${t("skills.empty.libraryHint")}`}
        onOpen={open}
        onInstall={onInstall}
        installingId={installingId}
        installLabel={(item) =>
          targets.get(item.id)?.kind === "library" ? t("skills.importOf", { name: item.name }) : t("catalogUi.install", { name: item.name })
        }
        renderNote={(item) => {
          const tg = targets.get(item.id);
          return tg ? <SkillCardNote target={tg} error={installError?.id === item.id ? installError.text : null} /> : null;
        }}
        renderAction={(item) => {
          const tg = targets.get(item.id);
          if (!tg) return null;
          if (tg.kind === "learned") return <LearnedActions stat={tg.stat} />;
          if (tg.kind === "bundle") return <BundleSwitch bundle={tg.bundle} unread={isUnread(tg.bundle, read)} />;
          if (tg.kind === "entry" && tg.entry.installed)
            return <BundleSwitch bundle={tg.bundle ?? { name: tg.entry.name, status: tg.entry.installed }} />;
          return null;
        }}
        mineHeader={
          <MineHeader
            skills={skills}
            learnedEmpty={learnedEmpty && mine.some((s) => s.items.length > 0)}
            onReview={() => setParams(withPatch({ view: null, section: "waiting" }))}
          />
        }
        discoverHeader={
          <DiscoverHeader
            library={library}
            catalog={catalog}
            pinned={pinned}
            fit={fit}
            grouping={grouping}
            onFit={(next) => setParams(withPatch({ fit: next }))}
            onGrouping={(next) => setParams(withPatch({ group: next === "name" ? "name" : null }))}
          />
        }
        notice={
          missing ? (
            <p role="status" className="flex items-center gap-2 text-xs text-warn-foreground">
              {t("catalogUi.itemMissing")}
              <Button size="sm" variant="ghost" onClick={() => setParams(withPatch({ item: null }))}>
                {t("common.close")}
              </Button>
            </p>
          ) : null
        }
      />
      <SkillDetail
        target={target}
        item={detailItem}
        onClose={close}
        read={read}
        onRead={markRead}
        onInstall={onInstall}
        installingId={installingId}
      />
    </>
  );
}
