import type { ChatMessage } from "../hooks/useChat";
import {
  isWriteTodosToolName,
  readWriteTodosSnapshot,
  type TodoListItem,
} from "../../../utils/parseWriteTodos";
import { isRoomHostSpeaker } from "./messageGrouping";

/** Project the current turn's latest plan independently of transcript scrolling. */
export function currentTodoProgress(
  messages: readonly ChatMessage[],
  hostAgentId?: string | null,
): { turnId: string; items: TodoListItem[] } | null {
  let start = messages.length - 1;
  while (
    start >= 0 &&
    (messages[start].role !== "user" || messages[start].steerPending)
  ) {
    start -= 1;
  }
  const turnId = messages[start]?.id ?? messages[0]?.id;
  if (!turnId) return null;

  let items: TodoListItem[] = [];
  for (let index = start + 1; index < messages.length; index += 1) {
    const message = messages[index];
    const tool = message.toolData;
    if (
      (message.role !== "assistant" && message.role !== "tool") ||
      message.status === "error" ||
      !isRoomHostSpeaker(message.speakerAgentId, hostAgentId ?? undefined) ||
      !tool ||
      !isWriteTodosToolName(tool.name)
    ) {
      continue;
    }
    const snapshot = readWriteTodosSnapshot(tool.arguments, tool.output);
    if (snapshot !== null) {
      // write_todos replaces the whole list, including removed steps or [].
      items = snapshot;
    } else if (message.status === "streaming") {
      const partial = readWriteTodosSnapshot(tool.arguments, tool.output, true);
      const byId = new Map(items.map((item) => [item.id, item]));
      for (const item of partial ?? []) byId.set(item.id, item);
      items = [...byId.values()];
    }
  }
  return items.length > 0 ? { turnId, items } : null;
}
