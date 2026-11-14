import type { ChatMessage } from "../hooks/sseHelpers";

function textOf(message: ChatMessage): string {
  return (message.content || "").trim();
}

/** Retain older pages only when their persisted boundary overlaps the new page. */
export function retainedHistoryPrefix(
  loaded: ChatMessage[],
  current: ChatMessage[],
): ChatMessage[] {
  const ids = new Set(loaded.map((message) => message.id));
  const overlap = current.findIndex((message) => ids.has(message.id));
  if (overlap <= 0 || current[overlap].localPending) return [];
  const prefix = current.slice(0, overlap);
  return prefix.every(
    (message) => !message.localPending && message.status !== "streaming",
  )
    ? prefix
    : [];
}

function assistantSize(messages: ChatMessage[]): number {
  return messages.reduce(
    (size, message) =>
      message.role !== "assistant" || message.toolData
        ? size
        : size +
          message.content.length +
          (message.contentBlocks ?? []).reduce(
            (sum, block) =>
              sum + (block.type === "text" ? (block.content || "").length : 0),
            0,
          ),
    0,
  );
}

/** Reconcile optimistic users on servers that assign their own message IDs. */
function reconcilePendingUsers(
  loaded: ChatMessage[],
  current: ChatMessage[],
): ChatMessage[] {
  const reconciled = [...current];
  let before = loaded.length;
  for (let i = current.length - 1; i >= 0; i--) {
    const local = current[i];
    if (local.role !== "user") continue;
    let match = loaded.findIndex((message) => message.id === local.id);
    if (match >= before) continue;
    // Preserve the existing text/time fallback, restricted to explicit local
    // sends. Consume each server turn once, in order, including partial saves.
    if (match < 0 && local.localPending && textOf(local)) {
      for (let j = before - 1; j >= 0; j--) {
        const server = loaded[j];
        if (
          server.role === "user" &&
          textOf(server) === textOf(local) &&
          (!server.timestamp || local.timestamp <= server.timestamp + 1000)
        ) {
          match = j;
          break;
        }
      }
    }
    if (match < 0) continue;
    before = match;
    if (local.localPending)
      reconciled[i] = { ...local, id: loaded[match].id, localPending: false };
  }
  return reconciled;
}

/** Interleave pending turns with history while preserving both message orders. */
function mergePendingTail(
  history: ChatMessage[],
  tail: ChatMessage[],
  afterAnchor: number,
): ChatMessage[] {
  const merged = history.slice(0, afterAnchor);
  const savedPositions = new Map(history.map(({ id }, index) => [id, index]));
  const nextSavedPosition = new Array<number>(tail.length);
  let nextSaved = history.length;
  for (let i = tail.length - 1; i >= 0; i--) {
    nextSavedPosition[i] = nextSaved;
    const position = savedPositions.get(tail[i].id);
    if (position !== undefined && position >= afterAnchor) nextSaved = position;
  }
  let next = afterAnchor;

  for (const [i, message] of tail.entries()) {
    const position = savedPositions.get(message.id);
    if (position !== undefined) {
      // Keep the server's copy at the matching position in the local turn.
      while (next <= position) merged.push(history[next++]);
      continue;
    }
    if (message.role === "user") {
      // A shared message ID sets an upper bound even if its timestamp ties
      // the question's or is missing. Compare timestamps for unmatched rows.
      while (next < nextSavedPosition[i]) {
        if (
          message.timestamp > 0 &&
          history[next].timestamp > message.timestamp
        ) {
          break;
        }
        merged.push(history[next]);
        next += 1;
      }
    }
    merged.push(message);
  }
  return [...merged, ...history.slice(next)];
}

/**
 * Apply a server history page without dropping a turn the user just sent.
 *
 * Opening an old thread fetches history before the optimistic bubble exists.
 * The response then replaces the list and the bubble vanishes, while the
 * WebSocket turn keeps running. Explicit pending sends stay on screen until
 * the server page includes them; unrelated cached turns do not append.
 */
export function mergeHistoryWithLocalTail(
  loaded: ChatMessage[],
  current: ChatMessage[],
  isStreaming = false,
): ChatMessage[] {
  if (current.length === 0) return loaded;

  const olderPrefix = retainedHistoryPrefix(loaded, current);
  current = reconcilePendingUsers(loaded, current);
  const loadedIds = new Set(loaded.map((message) => message.id));
  let lastOverlap = -1;
  let pending = -1;
  let overlapBeforePending = -1;
  for (let i = 0; i < current.length; i++) {
    const message = current[i];
    if (loadedIds.has(message.id)) {
      lastOverlap = i;
      // A refreshed page can include a reply while its user message is
      // still pending. Only a stored user turn supersedes an earlier send.
      if (message.role === "user") pending = -1;
    } else if (pending < 0 && message.role === "user" && message.localPending) {
      pending = i;
      overlapBeforePending = lastOverlap;
    }
  }

  const base = [...olderPrefix, ...loaded];
  if (pending >= 0) {
    const anchor = current[overlapBeforePending];
    const afterAnchor = anchor
      ? base.findIndex(({ id }) => id === anchor.id) + 1
      : 0;
    return mergePendingTail(base, current.slice(pending), afterAnchor);
  }
  if (lastOverlap < 0) return loaded;

  const localTail = current.slice(lastOverlap + 1);
  if (localTail.length === 0) return base;
  // A cached user row absent from the page is not proof of a newer turn.
  if (localTail.some((message) => message.role === "user")) return base;

  const overlapId = current[lastOverlap].id;
  const loadedOverlap = loaded.findIndex((message) => message.id === overlapId);
  const serverAfter = loadedOverlap >= 0 ? loaded.slice(loadedOverlap + 1) : [];
  if (serverAfter.length === 0) return [...base, ...localTail];
  const live =
    isStreaming || localTail.some((message) => message.status === "streaming");
  if (
    live &&
    !serverAfter.some((message) => message.role === "user") &&
    assistantSize(localTail) > assistantSize(serverAfter)
  ) {
    return [...base.slice(0, base.length - serverAfter.length), ...localTail];
  }
  return base;
}
