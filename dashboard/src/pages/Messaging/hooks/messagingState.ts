import type {
  CommunicationMessage,
  CommunicationUser,
  Conversation,
  SyncEvent,
} from "../../../api/types/communication";
export function compareSequence(a: string, b: string) {
  const x = BigInt(a),
    y = BigInt(b);
  return x < y ? -1 : x > y ? 1 : 0;
}
export function mergeMessages(
  current: CommunicationMessage[],
  incoming: CommunicationMessage[],
) {
  const byId = new Map(current.map((message) => [message.message_id, message]));
  for (const message of incoming) {
    const duplicate = current.find(
      (old) =>
        message.client_message_id &&
        old.client_message_id === message.client_message_id &&
        old.sender_id === message.sender_id,
    );
    if (duplicate && duplicate.message_id !== message.message_id)
      byId.delete(duplicate.message_id);
    const old = byId.get(message.message_id);
    byId.set(message.message_id, {
      ...old,
      ...message,
      recalled: !!old?.recalled || message.recalled,
    });
  }
  return [...byId.values()].sort((a, b) => compareSequence(a.seq, b.seq));
}
export function snapshotState(events: SyncEvent[]) {
  let profile: CommunicationUser | null = null;
  const contacts: Record<string, CommunicationUser> = {};
  const conversations: Record<string, Conversation> = {};
  for (const event of events) {
    if (event.type === "profile.updated")
      profile = event.payload as unknown as CommunicationUser;
    if (event.type === "contact.updated") {
      const value = event.payload as unknown as CommunicationUser;
      contacts[value.user_id] = value;
    }
    if (event.type === "conversation.created") {
      const value = event.payload as unknown as Conversation;
      conversations[value.conversation_id] = value;
    }
  }
  return { profile, contacts, conversations };
}

export function advanceConversationRead(
  conversation: Conversation,
  seq: string,
): Conversation {
  const previous = conversation.last_read_seq ?? "0";
  const lastRead = compareSequence(previous, seq) > 0 ? previous : seq;
  return {
    ...conversation,
    last_read_seq: lastRead,
    // Acknowledging an older message must not clear a newer unread badge.
    ...(compareSequence(lastRead, conversation.last_seq) >= 0
      ? { unread: 0 }
      : {}),
  };
}
export function conversationCanSend(
  conversation: Conversation,
  profile: CommunicationUser | null,
  contacts: Record<string, CommunicationUser>,
) {
  if (conversation.archived || conversation.status === "dissolved")
    return false;
  if (conversation.type === "private") {
    if (conversation.can_send !== undefined) return conversation.can_send;
    const peer = contacts[conversation.peer_user_id ?? ""];
    return !!peer && peer.can_message !== false && !!profile;
  }
  const epoch = conversation.membership_epochs?.find(
    (member) => member.left_at === null,
  );
  if (!epoch) return false;
  return (
    epoch.role === "owner" ||
    ((!epoch.mute_until || Date.parse(epoch.mute_until) <= Date.now()) &&
      (epoch.role !== "member" || !conversation.group?.mute_all))
  );
}
