export interface BrowserTabLike {
  id: number | string;
  url: string;
  title: string;
  active: boolean;
}

/**
 * Merge a browser tab update without letting backend/CDP ordering jitter move
 * existing tabs around. Existing tabs keep their current visual order, removed
 * tabs disappear, and newly discovered tabs are appended in incoming order.
 */
export function mergeBrowserTabsStable<T extends BrowserTabLike>(
  previousTabs: T[],
  incomingTabs: T[],
): T[] {
  if (incomingTabs.length === 0) {
    return previousTabs.length === 0 ? previousTabs : [];
  }

  const incomingById = new Map(
    incomingTabs.map((tab) => [String(tab.id), tab] as const),
  );
  const merged: T[] = [];
  const seen = new Set<string>();

  for (const prev of previousTabs) {
    const id = String(prev.id);
    const next = incomingById.get(id);
    if (!next) continue;
    merged.push(next);
    seen.add(id);
  }

  for (const next of incomingTabs) {
    const id = String(next.id);
    if (seen.has(id)) continue;
    merged.push(next);
    seen.add(id);
  }

  if (browserTabsEqual(previousTabs, merged)) {
    return previousTabs;
  }

  return merged;
}

function browserTabsEqual<T extends BrowserTabLike>(left: T[], right: T[]) {
  if (left.length !== right.length) return false;
  return left.every((tab, index) => {
    const other = right[index];
    return (
      other !== undefined &&
      String(tab.id) === String(other.id) &&
      tab.url === other.url &&
      tab.title === other.title &&
      tab.active === other.active
    );
  });
}

export function markBrowserTabActive<T extends BrowserTabLike>(
  tabs: T[],
  tabId: number | string,
): T[] {
  const targetId = String(tabId);
  let found = false;
  const next = tabs.map((tab) => {
    const active = String(tab.id) === targetId;
    if (active) found = true;
    return { ...tab, active };
  });
  return found ? next : tabs;
}

export function closeBrowserTabOptimistic<T extends BrowserTabLike>(
  tabs: T[],
  tabId: number | string,
): T[] {
  const targetId = String(tabId);
  const closingIndex = tabs.findIndex((tab) => String(tab.id) === targetId);
  if (closingIndex < 0) return tabs;

  const wasActive = tabs[closingIndex]?.active === true;
  const remaining = tabs.filter((tab) => String(tab.id) !== targetId);
  if (remaining.length === 0 || !wasActive) return remaining;

  const nextActiveIndex = Math.min(closingIndex, remaining.length - 1);
  return remaining.map((tab, index) => ({
    ...tab,
    active: index === nextActiveIndex,
  }));
}
