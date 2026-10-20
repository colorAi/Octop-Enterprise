import type { LocalArtifact, LocalArtifactTurn } from "@/api/desktopRuntime";
import type { ChatMessage } from "../hooks/useChat";

/** Compatibility for older runtimes whose done event contains the full project. */
export function changedLocalArtifacts(
  messages: ChatMessage[],
  files: LocalArtifact[],
  historicalFiles: LocalArtifact[] = [],
): LocalArtifact[] {
  const previous = new Map<string, string>();
  for (const message of messages)
    for (const file of message.localArtifacts ?? [])
      previous.set(file.path, file.sha256);
  for (const file of historicalFiles) previous.set(file.path, file.sha256);
  return files.filter(
    (file) => file.explicitly_sent || previous.get(file.path) !== file.sha256,
  );
}

/** Place receipts beside their own turn, never at the end of an unrelated page. */
export function placeLocalArtifactTurns(
  messages: ChatMessage[],
  turns: LocalArtifactTurn[],
): ChatMessage[] {
  const result = [...messages];
  for (const turn of turns) {
    if (!turn.files.length) continue;
    const id = `${turn.task_id}:local-artifacts`;
    const existing = result.findIndex((message) => message.id === id);
    if (existing >= 0) result.splice(existing, 1);
    const user = result.findIndex(
      (message) => message.id === turn.user_message_id,
    );
    let anchor = -1;
    if (user >= 0) {
      let end = user;
      while (end + 1 < result.length && result[end + 1].role !== "user")
        end += 1;
      anchor = result.findIndex(
        (message, index) =>
          index > user && index <= end && message.id === turn.message_id,
      );
      if (anchor < 0) anchor = end;
    } else if (turn.message_id) {
      // Pagination may begin inside the assistant round, after its user row.
      anchor = result.findIndex((message) => message.id === turn.message_id);
    }
    if (anchor < 0) continue; // Its turn is outside this history page.
    result.splice(anchor + 1, 0, {
      id,
      role: "assistant",
      content: "",
      localArtifacts: turn.files,
      localArtifactDeviceId: turn.device_id,
      timestamp: result[anchor]?.timestamp ?? Date.now(),
      speakerAgentId: result[anchor]?.speakerAgentId,
      status: "done",
    });
  }
  return result;
}
