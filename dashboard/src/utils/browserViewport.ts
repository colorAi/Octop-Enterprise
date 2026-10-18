import type { ViewportMode } from "../hooks/useViewportMode";

/** Human-readable viewport preset label (fixed sizes use Unicode ×). */
export function viewportModeLabel(
  mode: ViewportMode,
  t: (key: string) => string,
): string {
  if (mode === "auto") return t("browserWorkspace.viewportAuto");
  return mode.replace("x", "×");
}

export const REFRESH_INTERVAL_PRESETS = [0, 250, 500, 1000, 2000] as const;

export function refreshIntervalLabel(
  ms: number,
  t: (key: string) => string,
): string {
  if (ms === 0) return t("remoteBrowser.refreshManual");
  if (ms >= 1000) return `${ms / 1000}s`;
  return `${ms}ms`;
}
