import { getApiUrl } from "./config";

const DEFAULT_TIMEOUT_MS = 5_000;

export interface HealthProbeResult {
  ok: boolean;
  started_at?: number;
}

/**
 * Lightweight liveness probe for service-restart polling.
 * Uses raw fetch (no JWT, no redirect side effects) and never caches.
 */
export async function probeHealth(
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<HealthProbeResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(getApiUrl("/health"), {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      return { ok: false };
    }
    const body = (await response.json()) as {
      ok?: boolean;
      started_at?: number;
    };
    return {
      ok: body.ok === true,
      started_at:
        typeof body.started_at === "number" ? body.started_at : undefined,
    };
  } catch {
    return { ok: false };
  } finally {
    clearTimeout(timer);
  }
}
