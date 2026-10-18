import { useCallback, useEffect, useState } from "react";
import { updateApi, type UpdateStatus } from "../api/modules/update";
import {
  UPDATE_STATUS_CHANGED_EVENT,
  UPDATE_STATUS_POLL_MS,
  isUpdateStatusCacheExpired,
  readStoredUpdateStatus,
  storeUpdateStatus,
} from "../utils/updateStatusCache";

/** Shared in-flight probe so Header + Sidebar mounts don't stampede PyPI. */
let inFlight: Promise<UpdateStatus | null> | null = null;

async function probeUpdateStatus(): Promise<UpdateStatus | null> {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    try {
      const next = await updateApi.getUpdateStatus();
      storeUpdateStatus(next);
      return next;
    } catch {
      return null;
    } finally {
      inFlight = null;
    }
  })();
  return inFlight;
}

export function useUpdateStatus(enabled = true) {
  const [status, setStatus] = useState<UpdateStatus | null>(() =>
    enabled ? readStoredUpdateStatus() : null,
  );

  const refreshStatus = useCallback(
    async (force = false) => {
      if (!enabled) return;
      if (!force && !isUpdateStatusCacheExpired()) {
        const cached = readStoredUpdateStatus();
        if (cached) {
          setStatus(cached);
          return;
        }
      }
      const next = await probeUpdateStatus();
      if (next) setStatus(next);
    },
    [enabled],
  );

  useEffect(() => {
    if (!enabled) return;
    // Dashboard boot: always re-fetch so a restarted server is not hidden
    // by a still-fresh localStorage entry. Focus / interval still honor TTL.
    void refreshStatus(true);
  }, [enabled, refreshStatus]);

  useEffect(() => {
    if (!enabled) return;
    const onFocus = () => {
      void refreshStatus(false);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [enabled, refreshStatus]);

  // Keep checking while the dashboard stays open (cache TTL still gates PyPI).
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(() => {
      void refreshStatus(false);
    }, UPDATE_STATUS_POLL_MS);
    return () => window.clearInterval(id);
  }, [enabled, refreshStatus]);

  // Sync Header / Sidebar / Update page when any writer stores a new status.
  useEffect(() => {
    if (!enabled) return;
    const onChanged = (event: Event) => {
      const next = (event as CustomEvent<UpdateStatus>).detail;
      if (next) setStatus(next);
    };
    window.addEventListener(UPDATE_STATUS_CHANGED_EVENT, onChanged);
    return () =>
      window.removeEventListener(UPDATE_STATUS_CHANGED_EVENT, onChanged);
  }, [enabled]);

  const hasUpdate = Boolean(
    enabled && status?.has_update && status?.latest_version,
  );

  return { status: enabled ? status : null, hasUpdate, refreshStatus };
}
