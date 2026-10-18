import { useMemo, useState } from "react";
import type { DirectoryNode } from "./directoryTree";

function loadCollapsed(key: string): Set<string> {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
    if (Array.isArray(value))
      return new Set(
        value.filter((item): item is string => typeof item === "string"),
      );
  } catch {
    // Expansion remains usable when browser storage is unavailable.
  }
  return new Set();
}

/** Keep department choices across list remounts, separately from search results. */
export function useDirectoryExpansion(
  nodes: DirectoryNode[],
  owner: number | undefined,
  search: string,
) {
  const storageKey = `octop:contacts:collapsed:${owner ?? "anonymous"}`;
  const [saved, setSaved] = useState(() => ({
    key: storageKey,
    collapsed: loadCollapsed(storageKey),
  }));
  const normalCollapsed = useMemo(
    () =>
      saved.key === storageKey ? saved.collapsed : loadCollapsed(storageKey),
    [saved, storageKey],
  );
  const searchKey = `${storageKey}:${search.trim()}`;
  const [searched, setSearched] = useState({
    key: searchKey,
    collapsed: new Set<string>(),
  });
  const searching = Boolean(search.trim());
  const collapsed = searching
    ? searched.key === searchKey
      ? searched.collapsed
      : new Set<string>()
    : normalCollapsed;
  const branchKeys = useMemo(() => {
    const keys: string[] = [];
    const visit = (items: DirectoryNode[]) => {
      for (const node of items) {
        if (node.children?.length) {
          keys.push(node.key);
          visit(node.children);
        }
      }
    };
    visit(nodes);
    return keys;
  }, [nodes]);
  const onExpand = (key: string, expanded: boolean) => {
    const next = new Set(collapsed);
    if (expanded) next.delete(key);
    else next.add(key);
    if (searching) {
      setSearched({ key: searchKey, collapsed: next });
    } else {
      setSaved({ key: storageKey, collapsed: next });
      try {
        localStorage.setItem(storageKey, JSON.stringify([...next]));
      } catch {
        // Keep the current page state even if it cannot be persisted.
      }
    }
  };
  return {
    expandedKeys: branchKeys.filter((key) => !collapsed.has(key)),
    onExpand,
  };
}
