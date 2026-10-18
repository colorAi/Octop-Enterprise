/**
 * Pretty-print streamed tool-call arguments.
 *
 * Some providers emit each streaming chunk as a full JSON object; the chat
 * store concatenates them, producing invalid JSON like `{...}{...}`. Pick
 * the last parseable object for display when the full string won't parse.
 */
export function formatToolArguments(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";

  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    // Fall through — try last balanced `{...}` block.
  }

  let depth = 0;
  let start = -1;
  let lastObject: string | null = null;
  for (let i = 0; i < trimmed.length; i += 1) {
    const ch = trimmed[i];
    if (ch === "{") {
      if (depth === 0) start = i;
      depth += 1;
    } else if (ch === "}" && depth > 0) {
      depth -= 1;
      if (depth === 0 && start >= 0) {
        lastObject = trimmed.slice(start, i + 1);
        start = -1;
      }
    }
  }

  if (lastObject) {
    try {
      return JSON.stringify(JSON.parse(lastObject), null, 2);
    } catch {
      // ignore
    }
  }

  return trimmed;
}
