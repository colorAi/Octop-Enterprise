import type { TFunction } from "i18next";
import type { Conversation } from "../../api/types/communication";
import type { MessagingState } from "./hooks/communicationClient";
import { communicationError } from "../../api/modules/communication";
import { compareSequence } from "./hooks/messagingState";

export function communicationErrorText(error: unknown, t: TFunction): string {
  const value = communicationError(error);
  const key: Record<string, string> = {
    USER_DISABLED: "account_disabled",
    PERMISSION_DENIED: "forbidden",
    AUTH_FAILED: "session_invalid",
    TOKEN_EXPIRED: "session_invalid",
  };
  return t(`communication.errors.${key[value.code] ?? value.code}`, {
    defaultValue: t(
      value.status === 403
        ? "communication.errors.forbidden"
        : "communication.errors.unavailable",
    ),
  });
}
export function conversationName(
  row: Conversation,
  state: MessagingState,
  t: TFunction,
) {
  return row.type === "group"
    ? row.group?.name || t("communication.departmentGroup")
    : state.contacts[row.peer_user_id ?? ""]?.nickname ||
        t("communication.colleague");
}
export function visibleConversations(
  state: MessagingState,
  { includeHidden = false }: { includeHidden?: boolean } = {},
) {
  return Object.values(state.conversations)
    .filter(
      (row) =>
        includeHidden ||
        row.hidden_at_seq == null ||
        compareSequence(row.last_seq, row.hidden_at_seq) > 0,
    )
    .sort(
      (a, b) =>
        Number(b.pinned) - Number(a.pinned) ||
        (b.updated_at ?? "").localeCompare(a.updated_at ?? "") ||
        compareSequence(b.last_seq, a.last_seq),
    );
}

export function communicationUnreadCount(
  state: Pick<MessagingState, "conversations">,
) {
  return Object.values(state.conversations).reduce(
    (count, row) =>
      count +
      (row.muted ||
      (row.hidden_at_seq != null &&
        compareSequence(row.last_seq, row.hidden_at_seq) <= 0)
        ? 0
        : row.unread ?? 0),
    0,
  );
}
