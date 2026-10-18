/**
 * Shared HTTP header utilities.
 * Used to format, parse, and compare headers in provider config.
 */

/**
 * Format a header object as multi-line text, one Key: Value pair per line, sorted by key.
 */
export function formatHeadersText(headers?: Record<string, string>): string {
  if (!headers) return "";
  return Object.entries(headers)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");
}

/**
 * Parse multi-line text into a header object. Each line must be Key: Value.
 * @throws Error when any line has an invalid format.
 */
export function parseHeadersText(value?: string): Record<string, string> {
  const raw = (value || "").trim();
  if (!raw) return {};

  const headers: Record<string, string> = {};
  for (const line of raw.split(/\r?\n/)) {
    const current = line.trim();
    if (!current) continue;
    const idx = current.indexOf(":");
    if (idx <= 0) {
      throw new Error(`Invalid header line: ${current}`);
    }
    const key = current.slice(0, idx).trim();
    const headerValue = current.slice(idx + 1).trim();
    if (!key || !headerValue) {
      throw new Error(`Invalid header line: ${current}`);
    }
    headers[key] = headerValue;
  }
  return headers;
}

/**
 * Compare two header objects for equality after sorting keys.
 */
export function isEqualHeaders(
  a?: Record<string, string>,
  b?: Record<string, string>,
): boolean {
  const left = a || {};
  const right = b || {};
  const leftKeys = Object.keys(left).sort((a, b) => a.localeCompare(b));
  const rightKeys = Object.keys(right).sort((a, b) => a.localeCompare(b));
  if (leftKeys.length !== rightKeys.length) return false;
  for (let i = 0; i < leftKeys.length; i += 1) {
    const key = leftKeys[i];
    if (key !== rightKeys[i] || left[key] !== right[key]) return false;
  }
  return true;
}
