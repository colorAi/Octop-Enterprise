import type { ChatMessage } from "../hooks/sseHelpers";

function textOf(message: ChatMessage): string {
  return (message.content || "").trim();
}

/** First locally created user message the server page has not acknowledged. */
function newSendIndex(current: ChatMessage[], loadedIds: Set<string>): number {
  return current.findIndex(
    (message) =>
      message.role === "user" &&
      message.localPending &&
      !loadedIds.has(message.id),
  );
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
  const firstOverlap = current.findIndex((message) =>
    loadedIds.has(message.id),
  );

  if (firstOverlap < 0) {
    const pending = newSendIndex(current, loadedIds);
    if (pending < 0) return loaded;
    return [...loaded, ...current.slice(pending)];
  }

  let lastOverlap = firstOverlap;
  for (let i = current.length - 1; i >= 0; i--) {
    if (loadedIds.has(current[i].id)) {
      lastOverlap = i;
      break;
    }
  }

  const localTail = current.slice(lastOverlap + 1);
  const base = [...olderPrefix, ...loaded];
  if (localTail.length === 0) return base;

  const pending = newSendIndex(localTail, loadedIds);
  if (pending >= 0) {
    return [...base, ...localTail.slice(pending)];
  }
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
