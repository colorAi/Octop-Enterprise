type Cursor = { execution_id: string; event_seq: number; event_index: number };
const cursors = new Map<string, Cursor>();

export function executionCursor(threadId: string): Partial<Cursor> {
  return cursors.get(threadId) ?? {};
}

export function acceptExecutionFrame(
  threadId: string,
  frame: Record<string, unknown>,
): boolean {
  const { execution_id, event_seq, event_index } = frame;
  if (
    typeof execution_id !== "string" ||
    !Number.isInteger(event_seq) ||
    !Number.isInteger(event_index)
  )
    return true;
  const next = {
    execution_id,
    event_seq: event_seq as number,
    event_index: event_index as number,
  };
  const previous = cursors.get(threadId);
  if (
    previous?.execution_id === execution_id &&
    (next.event_seq < previous.event_seq ||
      (next.event_seq === previous.event_seq &&
        next.event_index <= previous.event_index))
  )
    return false;
  cursors.delete(threadId);
  cursors.set(threadId, next);
  if (cursors.size > 256) cursors.delete(cursors.keys().next().value!);
  return true;
}
