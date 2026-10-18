import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { DownloadState } from "../api/types/embedding";

/**
 * Hook for polling embedding model download status via HTTP.
 *
 * Keep polling until the component unmounts. Stopping at done/failed can miss a
 * later download click, leaving the progress bar stale.
 *
 * - loading/downloading: poll every 1s for responsive progress.
 * - other states: poll every 2s to reduce idle requests.
 */
export const useEmbeddingDownloadWS = () => {
  const [downloadState, setDownloadState] = useState<DownloadState | null>(
    null,
  );
  const wsConnected = true;
  const [wsError, setWsError] = useState<string | null>(null);

  /** Refresh immediately after the start-download action, without waiting for the next poll. */
  const refreshDownloadStatus = useCallback(async () => {
    try {
      const state = await api.getDownloadStatus();
      setDownloadState(state);
      setWsError(null);
      return state;
    } catch (err) {
      console.error("[useEmbeddingDownloadWS] Refresh error:", err);
      setWsError("Failed to fetch download status");
      return null;
    }
  }, []);

  useEffect(() => {
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let unmounted = false;

    const scheduleNext = (delayMs: number) => {
      if (pollTimer !== null) {
        clearTimeout(pollTimer);
      }
      pollTimer = setTimeout(poll, delayMs);
    };

    const poll = async () => {
      if (unmounted) return;

      try {
        const state = await api.getDownloadStatus();
        if (!unmounted) {
          setDownloadState(state);
          setWsError(null);
        }

        const nextDelay =
          state.status === "loading" || state.status === "downloading"
            ? 1000
            : 2000;
        scheduleNext(nextDelay);
      } catch (err) {
        if (!unmounted) {
          console.error("[useEmbeddingDownloadWS] Poll error:", err);
          setWsError("Failed to fetch download status");
          scheduleNext(3000);
        }
      }
    };

    poll();

    return () => {
      unmounted = true;
      if (pollTimer !== null) {
        clearTimeout(pollTimer);
      }
    };
  }, []);

  return { downloadState, wsConnected, wsError, refreshDownloadStatus };
};
