import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  expertAssignmentsApi,
  type ExpertAssignment,
  type ExpertAssignmentOverview,
  type ExpertResourceCatalog,
} from "../../../api/modules/expertAssignments";
import { apiErrorMessage } from "../../../utils/apiError";

export function useExpertAssignments(enabled: boolean, userId?: number) {
  const { t } = useTranslation();
  const translate = useRef(t);
  translate.current = t;
  const active = useRef({ enabled, userId });
  active.current = { enabled, userId };
  const controllers = useRef<
    Partial<Record<"directory" | "catalog", AbortController>>
  >({});
  const [state, setState] = useState<{
    userId?: number;
    directory?: ExpertAssignmentOverview;
    catalog?: ExpertResourceCatalog;
    directoryError?: string;
    catalogError?: string;
    catalogLoading?: boolean;
  }>({});

  const load = useCallback(
    async (part: "directory" | "catalog", signal?: AbortSignal) => {
      if (!enabled || signal?.aborted) return;
      controllers.current[part]?.abort();
      const controller = new AbortController();
      controllers.current[part] = controller;
      const abort = () => controller.abort();
      signal?.addEventListener("abort", abort, { once: true });
      const current = () =>
        !controller.signal.aborted &&
        active.current.enabled &&
        active.current.userId === userId;
      if (part === "catalog")
        setState((prev) => ({ ...prev, catalogLoading: true }));
      try {
        const value = await expertAssignmentsApi[part](controller.signal);
        if (current())
          setState((prev) => ({
            ...(prev.userId === userId ? prev : {}),
            userId,
            [part]: value,
            [`${part}Error`]: "",
          }));
      } catch (error) {
        if (current())
          setState((prev) => ({
            ...prev,
            userId,
            [`${part}Error`]: apiErrorMessage(
              error,
              translate.current("common.loadFailed"),
              translate.current,
            ),
          }));
        throw error;
      } finally {
        signal?.removeEventListener("abort", abort);
        if (current() && part === "catalog")
          setState((prev) => ({ ...prev, catalogLoading: false }));
      }
    },
    [enabled, userId],
  );

  const reload = useCallback(
    async (signal?: AbortSignal) => {
      const results = await Promise.allSettled([
        load("directory", signal),
        load("catalog", signal),
      ]);
      for (const result of results)
        if (result.status === "rejected") throw result.reason;
    },
    [load],
  );

  useEffect(() => {
    const controller = new AbortController();
    const requests = controllers.current;
    setState({ userId });
    if (enabled) void reload(controller.signal).catch(() => {});
    return () => {
      controller.abort();
      for (const request of Object.values(requests)) request?.abort();
    };
  }, [enabled, userId, reload]);

  const updateEmployees = useCallback(
    (employees: ExpertAssignment[]) => {
      if (active.current.userId !== userId || !active.current.enabled) return;
      setState((prev) =>
        !prev.directory || prev.userId !== userId
          ? prev
          : {
              ...prev,
              directory: {
                ...prev.directory,
                employees: prev.directory.employees.map(
                  (row) => employees.find((e) => e.id === row.id) ?? row,
                ),
              },
            },
      );
    },
    [userId],
  );

  const visible = enabled && state.userId === userId ? state : {};
  const matching =
    visible.catalog &&
    visible.directory &&
    visible.catalog.source.source_user_id ===
      visible.directory.source.source_user_id &&
    visible.catalog.source.revision === visible.directory.source.revision;
  const mismatch =
    visible.catalog &&
    visible.directory &&
    !matching &&
    !visible.catalogLoading;
  return {
    data: visible.directory && {
      ...visible.directory,
      ...(matching ? visible.catalog : {}),
    },
    catalogReady:
      !!matching && !visible.catalogError && !visible.catalogLoading,
    catalogLoading: !!visible.catalogLoading,
    error:
      visible.directoryError ||
      visible.catalogError ||
      (mismatch ? t("expertAccess.errors.policy_revision_conflict") : ""),
    reload,
    updateEmployees,
  };
}
