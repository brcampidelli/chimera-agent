import { useState } from "react";

import { triState } from "@/components/ui/checkbox";

/**
 * What the Code sidebar has ticked while it is in selection mode.
 *
 * Two sets: conversations by id, and folders by key. A folder with conversations is "ticked" when
 * all of them are, so its header box is derived, not stored; only a folder with NO conversation
 * needs a set of its own, because there is nothing under it to tick. The sets are only ever read
 * through what the list shows now (`count`, `stateOf`), so a conversation that was deleted or filtered
 * away stops counting without anyone having to prune.
 */
export function useSessionSelection() {
  const [selecting, setSelecting] = useState(false);
  const [ids, setIds] = useState<Set<string>>(() => new Set());
  const [folders, setFolders] = useState<Set<string>>(() => new Set());

  function clear() {
    setIds(new Set());
    setFolders(new Set());
  }
  function exit() {
    setSelecting(false);
    clear();
  }
  function setMany(list: readonly string[], on: boolean) {
    setIds((prev) => {
      const next = new Set(prev);
      for (const id of list) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }
  function setFolder(key: string, on: boolean) {
    setFolders((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }
  return {
    selecting,
    toggle: () => (selecting ? exit() : setSelecting(true)),
    exit,
    clear,
    ids,
    folders,
    has: (id: string) => ids.has(id),
    setMany,
    setFolder,
    /** The box over a list of ids: none, some, all. */
    stateOf: (list: readonly string[]) => triState(list.filter((id) => ids.has(id)).length, list.length),
  };
}

export type SessionSelection = ReturnType<typeof useSessionSelection>;
