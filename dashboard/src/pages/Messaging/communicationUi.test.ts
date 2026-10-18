import { expect, it } from "vitest";
import { communicationUnreadCount } from "./communicationUi";
import type { Conversation } from "../../api/types/communication";

it("counts new messages that make a hidden conversation visible again", () => {
  const row: Conversation = {
    conversation_id: "c",
    type: "private",
    pinned: false,
    muted: false,
    hidden_at_seq: "9007199254740992",
    last_seq: "9007199254740992",
    unread: 1,
  };
  expect(communicationUnreadCount({ conversations: { c: row } })).toBe(0);
  row.last_seq = "9007199254740993";
  expect(communicationUnreadCount({ conversations: { c: row } })).toBe(1);
  row.muted = true;
  expect(communicationUnreadCount({ conversations: { c: row } })).toBe(0);
});
