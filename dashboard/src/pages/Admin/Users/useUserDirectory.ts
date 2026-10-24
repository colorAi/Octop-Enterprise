import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { usersApi, type UserRow } from "../../../api/modules/users";
import { apiErrorMessage } from "../../../utils/apiError";

export function useUserDirectory(identity: number | null, search: string) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [pagination, setPagination] = useState({ page: 1, pageSize: 25 });
  const { page, pageSize } = pagination;
  const [data, setData] = useState<{
    identity: number | null;
    rows: UserRow[];
    total: number;
  }>({
    identity,
    rows: [],
    total: 0,
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const controller = useRef<AbortController>();

  useEffect(() => {
    if (search.trim() === query) return;
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setPagination((prev) => ({ ...prev, page: 1 }));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [search, query]);

  useEffect(() => {
    setData({ identity, rows: [], total: 0 });
    setPagination({ page: 1, pageSize: 25 });
    setError("");
  }, [identity]);

  const refresh = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError("");
    try {
      const result = await usersApi.page(page, pageSize, query, current.signal);
      if (current.signal.aborted) return;
      const last = Math.max(1, Math.ceil(result.total / pageSize));
      if (page > last) {
        setPagination((prev) => ({ ...prev, page: last }));
        return;
      }
      setData({ identity, rows: result.items, total: result.total });
    } catch (err) {
      if (!current.signal.aborted)
        setError(apiErrorMessage(err, t("adminUsers.loadFailed"), t));
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }, [identity, page, pageSize, query, t]);

  useEffect(() => {
    void refresh();
    return () => controller.current?.abort();
  }, [refresh]);

  const changePage = (nextPage: number, nextSize: number) => {
    setPagination({
      page: nextSize === pageSize ? nextPage : 1,
      pageSize: nextSize,
    });
  };
  return {
    rows: data.identity === identity ? data.rows : [],
    total: data.identity === identity ? data.total : 0,
    page,
    pageSize,
    changePage,
    loading,
    error,
    refresh,
  };
}
