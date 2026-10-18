export function getMentionAtCursor(
  text: string,
): { query: string; atIndex: number } | null {
  const match = /(?:^|\s)@([^\s@]*)$/.exec(text);
  if (!match) return null;
  const query = match[1];
  const atIndex = text.length - query.length - 1;
  return { query, atIndex };
}
