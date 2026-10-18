import { useMemo, useState } from "react";

export function useFilteredList<T>(
  items: T[],
  filterFn: (item: T, query: string) => boolean,
) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return items;
    return items.filter((item) => filterFn(item, q));
  }, [items, query, filterFn]);

  return { query, setQuery, filtered };
}
