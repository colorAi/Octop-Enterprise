import { useCallback, useState } from "react";

/**
 * Selectable viewport size for the remote-browser screencast.
 *
 * - ``auto``: Chrome viewport tracks the container size live (ResizeObserver
 *   → backend ``Emulation.setDeviceMetricsOverride``). Best on large
 *   panes — no letterboxing, no upscaling.
 * - Fixed presets: Chrome viewport pinned to a stable resolution. Best for
 *   small panels (the chat popup) where the container is too cramped to
 *   render desktop sites legibly. The canvas is then letterboxed inside
 *   the container via ``flex`` centering.
 */
export type ViewportMode = "auto" | "1280x800" | "1440x900" | "1920x1080";

export interface ViewportModeOption {
  value: ViewportMode;
  label: string;
  /** ``null`` for ``auto`` (caller derives from container size). */
  width: number | null;
  height: number | null;
}

export const VIEWPORT_MODE_OPTIONS: ViewportModeOption[] = [
  { value: "auto", label: "", width: null, height: null },
  { value: "1280x800", label: "1280×800", width: 1280, height: 800 },
  { value: "1440x900", label: "1440×900", width: 1440, height: 900 },
  { value: "1920x1080", label: "1920×1080", width: 1920, height: 1080 },
];

const isViewportMode = (v: unknown): v is ViewportMode =>
  typeof v === "string" && VIEWPORT_MODE_OPTIONS.some((o) => o.value === v);

/**
 * Track the user's preferred viewport mode for a remote-browser surface.
 *
 * ``storageKey`` lets the chat popup and the standalone page have
 * independent preferences (the popup defaults to a fixed preset because
 * it lives in a small pane; the standalone page defaults to ``auto``).
 */
export function useViewportMode(
  storageKey: string,
  defaultMode: ViewportMode = "auto",
): {
  mode: ViewportMode;
  setMode: (m: ViewportMode) => void;
  /** Resolve to a concrete ``(width, height)`` given the container size.
   *  Returns ``null`` when the container hasn't been measured yet. */
  resolve: (
    containerW: number,
    containerH: number,
  ) => { width: number; height: number } | null;
} {
  const [mode, setModeState] = useState<ViewportMode>(() => {
    try {
      const saved = localStorage.getItem(storageKey);
      if (isViewportMode(saved)) return saved;
    } catch {
      // localStorage unavailable
    }
    return defaultMode;
  });

  const setMode = useCallback(
    (m: ViewportMode) => {
      setModeState(m);
      try {
        localStorage.setItem(storageKey, m);
      } catch {
        // ignore quota / disabled
      }
    },
    [storageKey],
  );

  const resolve = useCallback(
    (containerW: number, containerH: number) => {
      if (mode === "auto") {
        if (containerW <= 0 || containerH <= 0) return null;
        return { width: containerW, height: containerH };
      }
      const opt = VIEWPORT_MODE_OPTIONS.find((o) => o.value === mode);
      if (!opt || opt.width == null || opt.height == null) return null;
      return { width: opt.width, height: opt.height };
    },
    [mode],
  );

  return { mode, setMode, resolve };
}
