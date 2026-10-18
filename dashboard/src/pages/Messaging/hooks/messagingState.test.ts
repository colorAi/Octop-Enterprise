import { describe, it, expect } from "vitest";
import type {
  CommunicationMessage,
  Conversation,
  SyncEvent,
} from "../../../api/types/communication";
import {
  compareSequence,
  mergeMessages,
  snapshotState,
  conversationCanSend,
} from "./messagingState";
const message = (seq: string, id = seq): CommunicationMessage => ({
  message_id: id,
  seq,
  sender_id: "a",
  type: "text",
  text: "hello",
  recalled: false,
  created_at: "2026-10-12T10:00:00Z",
  client_message_id: "cid",
});
describe("messaging state", () => {
  it("preserves int64 precision and deduplicates receipts and sync history", () => {
    expect(compareSequence("9007199254740993", "9007199254740992")).toBe(1);
    const result = mergeMessages(
      [message("9007199254740993", "receipt")],
      [message("9007199254740993", "canonical")],
    );
    expect(result.map((m) => m.message_id)).toEqual(["canonical"]);
    expect(
      mergeMessages(
        [{ ...message("1"), recalled: true, text: null }],
        [message("1")],
      )[0].recalled,
    ).toBe(true);
  });
  it("applies all snapshot entries even when they share one sequence", () => {
    const events = [
      { type: "profile.updated", payload: { user_id: "a", nickname: "A" } },
      { type: "contact.updated", payload: { user_id: "b", nickname: "B" } },
      {
        type: "conversation.created",
        payload: { conversation_id: "c", type: "private", last_seq: "0" },
      },
    ].map((e) => ({
      ...e,
      seq: "9",
      object_id: "",
      version: "0",
    })) as SyncEvent[];
    const result = snapshotState(events);
    expect(result.profile?.user_id).toBe("a");
    expect(result.contacts.b.nickname).toBe("B");
    expect(result.conversations.c.conversation_id).toBe("c");
  });
  it("blocks archived, muted and departed group memberships", () => {
    const group = {
      conversation_id: "g",
      type: "group",
      last_seq: "0",
      pinned: false,
      muted: false,
      membership_epochs: [
        {
          role: "member",
          epoch: 1,
          left_at: null,
          mute_until: null,
          visible_from_seq: "0",
          visible_through_seq: null,
        },
      ],
      group: { mute_all: true },
    } as Conversation;
    expect(conversationCanSend(group, null, {})).toBe(false);
    expect(
      conversationCanSend(
        {
          ...group,
          membership_epochs: [
            { ...group.membership_epochs![0], role: "admin" },
          ],
        },
        null,
        {},
      ),
    ).toBe(true);
    expect(conversationCanSend({ ...group, archived: true }, null, {})).toBe(
      false,
    );
    expect(
      conversationCanSend(
        {
          ...group,
          membership_epochs: [
            { ...group.membership_epochs![0], left_at: "2026-01-01" },
          ],
        },
        null,
        {},
      ),
    ).toBe(false);
  });

  it("enforces personal administrator mutes without removing their mute-all exemption", () => {
    const group = {
      conversation_id: "g",
      type: "group",
      last_seq: "1",
      pinned: false,
      muted: false,
      group: { mute_all: true },
      membership_epochs: [
        {
          epoch: 1,
          role: "admin",
          left_at: null,
          mute_until: new Date(Date.now() + 3600_000).toISOString(),
          visible_from_seq: "1",
          visible_through_seq: null,
        },
      ],
    } as Conversation;
    expect(conversationCanSend(group, null, {})).toBe(false);
    for (const mute_until of [
      null,
      new Date(Date.now() - 1000).toISOString(),
    ]) {
      expect(
        conversationCanSend(
          {
            ...group,
            membership_epochs: [{ ...group.membership_epochs![0], mute_until }],
          },
          null,
          {},
        ),
      ).toBe(true);
    }
    // Transferring ownership must not leave the new owner unable to speak.
    expect(
      conversationCanSend(
        {
          ...group,
          membership_epochs: [
            { ...group.membership_epochs![0], role: "owner" },
          ],
        },
        null,
        {},
      ),
    ).toBe(true);
  });
});
