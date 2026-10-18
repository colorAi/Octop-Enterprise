import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  communicationApi,
  CommunicationError,
} from "../../../api/modules/communication";
import { CommunicationClient } from "./communicationClient";
import { connectCommunicationRealtime } from "../../../api/communicationRealtime";
import type {
  CommunicationMessage,
  SendReceipt,
  SyncEvent,
} from "../../../api/types/communication";
const auth = vi.hoisted(() => ({ token: "" }));
vi.mock("../../../api/request", () => ({
  getAuthToken: () => auth.token,
  request: vi.fn(),
  requestBlob: vi.fn(),
}));
vi.mock("../../../api/communicationRealtime", () => ({
  connectCommunicationRealtime: vi.fn(() => vi.fn()),
}));
const own = "octop:i:1",
  peer = "octop:i:2";
const conv = {
  conversation_id: "c",
  type: "private" as const,
  peer_user_id: peer,
  last_seq: "0",
  pinned: false,
  muted: false,
  can_send: true,
};
const event = (
  type: string,
  seq = "10",
  payload: Record<string, unknown> = {},
): SyncEvent => ({ type, seq, object_id: "m", version: "0", payload });
const msg = (seq: string): CommunicationMessage => ({
  message_id: `m${seq}`,
  seq,
  sender_id: peer,
  type: "text",
  text: `message${seq}`,
  recalled: false,
  created_at: "2026-10-12T10:00:00Z",
});
let client: CommunicationClient;
beforeEach(() => {
  auth.token = `header.${btoa(
    JSON.stringify({ sub: 1, sid: "session" }),
  )}.signature`;
  vi.spyOn(communicationApi, "snapshot").mockResolvedValue({
    snapshot_id: "s",
    until_seq: "9",
    cursor: "cursor9",
    chunk_count: 2,
  });
  vi.spyOn(communicationApi, "chunk").mockImplementation(async (_, chunk) => ({
    snapshot_id: "s",
    chunk_index: chunk,
    chunk_count: 2,
    events:
      chunk === 0
        ? [
            event("profile.updated", "9", { user_id: own, nickname: "A" }),
            event("contact.updated", "9", {
              user_id: peer,
              nickname: "B",
              can_message: true,
            }),
          ]
        : [event("conversation.created", "9", conv)],
  }));
  vi.spyOn(communicationApi, "conversations").mockResolvedValue({
    items: [conv],
    has_more: false,
  });
  vi.spyOn(communicationApi, "contacts").mockResolvedValue({
    items: [{ user_id: peer, nickname: "B", can_message: true }],
  });
  vi.spyOn(communicationApi, "departments").mockResolvedValue({ items: [] });
  vi.spyOn(communicationApi, "presence").mockResolvedValue({
    online_user_ids: [peer],
  });
  vi.spyOn(communicationApi, "directorySettings").mockResolvedValue({
    name: "示例公司",
  });
  vi.spyOn(communicationApi, "sync").mockResolvedValue({
    events: [],
    cursor: "cursor9",
    head_seq: "9",
    has_more: false,
  });
  vi.spyOn(communicationApi, "history").mockResolvedValue({
    items: [msg("1")],
    has_more: false,
    next_before_seq: "1",
  });
  client = new CommunicationClient("1:session", true, true);
});
afterEach(() => {
  client.stop();
  vi.restoreAllMocks();
});
const boot = async () => {
  client.start();
  await client.synchronize();
};
describe("communication synchronization", () => {
  it("refreshes presence without requiring durable sync events", async () => {
    await boot();
    expect(client.state.presence).toEqual({ [peer]: true });
    vi.mocked(communicationApi.presence).mockResolvedValue({
      online_user_ids: [],
    });
    await client.synchronize();
    expect(client.state.presence).toEqual({});
    vi.mocked(communicationApi.presence).mockRejectedValue(
      new Error("network"),
    );
    await client.synchronize();
    expect(client.state.presence).toBeNull();
    expect(client.state.phase).toBe("ready");
    expect(client.state.contacts[peer]).toBeDefined();
  });
  it("clears stale presence on disconnect and refreshes on reconnect", async () => {
    await boot();
    const onStatus = vi
      .mocked(connectCommunicationRealtime)
      .mock.calls.at(-1)![2];
    onStatus("retrying");
    expect(client.state.presence).toBeNull();
    onStatus("online");
    await client.synchronize();
    expect(client.state.presence).toEqual({ [peer]: true });
    client.stop();
    expect(client.state.presence).toBeNull();
  });
  it("connects contacts-only users without loading messaging data", async () => {
    client = new CommunicationClient("1:session", true, false);
    vi.mocked(connectCommunicationRealtime).mockClear();
    await boot();
    expect(connectCommunicationRealtime).toHaveBeenCalled();
    expect(communicationApi.snapshot).not.toHaveBeenCalled();
    expect(communicationApi.sync).not.toHaveBeenCalled();
    expect(client.state.presence).toEqual({ [peer]: true });
  });
  it("opens an existing direct chat without waiting for any metadata requests", async () => {
    await boot();
    const direct = vi.spyOn(communicationApi, "direct");
    vi.mocked(communicationApi.conversations).mockClear();
    vi.mocked(communicationApi.sync).mockClear();
    expect(await client.direct(peer)).toBe("c");
    expect(direct).not.toHaveBeenCalled();
    expect(communicationApi.conversations).not.toHaveBeenCalled();
    expect(communicationApi.sync).not.toHaveBeenCalled();
  });
  it("opens a newly authorized direct chat while background sync is still waiting", async () => {
    await boot();
    vi.spyOn(communicationApi, "direct").mockResolvedValue({
      conversation_id: "new",
    });
    let finish!: (
      value: Awaited<ReturnType<typeof communicationApi.sync>>,
    ) => void;
    vi.mocked(communicationApi.sync).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    expect(await client.direct("octop:i:3")).toBe("new");
    expect(client.state.conversations.new.peer_user_id).toBe("octop:i:3");
    expect(client.state.phase).toBe("ready");
    finish({ events: [], cursor: "cursor9", head_seq: "9", has_more: false });
    await client.synchronize();
  });
  it("loads directory metadata alongside the conversation list", async () => {
    let finish!: (
      value: Awaited<ReturnType<typeof communicationApi.conversations>>,
    ) => void;
    vi.mocked(communicationApi.conversations).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    client.start();
    await vi.waitFor(() =>
      expect(communicationApi.conversations).toHaveBeenCalled(),
    );
    expect(communicationApi.contacts).toHaveBeenCalled();
    expect(communicationApi.departments).toHaveBeenCalled();
    finish({ items: [conv], has_more: false });
    await client.synchronize();
    expect(client.state.phase).toBe("ready");
  });
  it("loads every snapshot chunk and isolates expired sessions", async () => {
    await boot();
    expect(client.state.phase).toBe("ready");
    expect(client.state.profile?.user_id).toBe(own);
    expect(client.state.contacts[peer].nickname).toBe("B");
    expect(client.state.conversations.c).toBeDefined();
    client.scrollPositions.set("c", 123);
    client.stop();
    expect(client.scrollPositions.size).toBe(0);
    expect(client.state.histories).toEqual({});
    expect(client.state.contacts).toEqual({});
    expect(client.state.profile).toBeNull();
  });
  it("retries incomplete bootstrap after metadata failure", async () => {
    vi.mocked(communicationApi.contacts).mockRejectedValueOnce(
      new Error("network"),
    );
    await boot();
    expect(client.state.phase).toBe("error");
    await client.synchronize();
    expect(client.state.phase).toBe("ready");
  });
  it("commits cursor and sequence only after history materialization succeeds", async () => {
    await boot();
    client.focus("c");
    await client.loadHistory("c");
    vi.mocked(communicationApi.sync).mockResolvedValue({
      events: [event("message.new", "10", { conversation_id: "c" })],
      cursor: "cursor10",
      head_seq: "10",
      has_more: false,
    });
    vi.mocked(communicationApi.history).mockRejectedValueOnce(
      new Error("network"),
    );
    await client.synchronize();
    expect(client.state.sequence).toBe("9");
    vi.mocked(communicationApi.history).mockResolvedValue({
      items: [msg("2"), msg("1")],
      has_more: false,
      next_before_seq: "1",
    });
    await client.synchronize();
    expect(client.state.sequence).toBe("10");
    expect(client.state.histories.c.map((m) => m.seq)).toEqual(["1", "2"]);
    expect(communicationApi.sync).toHaveBeenLastCalledWith(
      "cursor9",
      expect.any(AbortSignal),
    );
  });
  it("keeps the same send identifier for a retry and merges the canonical response", async () => {
    await boot();
    vi.spyOn(communicationApi, "send")
      .mockRejectedValueOnce(new Error("connection lost"))
      .mockResolvedValue({
        message_id: "sent",
        seq: "2",
        created_at: "2026-10-12T10:00:00Z",
        conversation_id: "c",
      });
    const body = { client_message_id: "stable", text: "hello" };
    await expect(client.send("c", body)).rejects.toBeDefined();
    expect(client.state.pending.stable.status).toBe("failed");
    await client.send("c", client.state.pending.stable.body);
    expect(communicationApi.send).toHaveBeenNthCalledWith(
      2,
      "c",
      body,
      expect.any(AbortSignal),
    );
    expect(client.state.pending.stable).toBeUndefined();
    expect(
      client.state.histories.c.filter((m) => m.message_id === "sent"),
    ).toHaveLength(1);
  });
  it("bridges history gaps across multiple pages after reconnect", async () => {
    await boot();
    await client.loadHistory("c");
    vi.mocked(communicationApi.history)
      .mockResolvedValueOnce({
        items: [msg("100"), msg("51")],
        has_more: true,
        next_before_seq: "51",
      })
      .mockResolvedValueOnce({
        items: [msg("50"), msg("1")],
        has_more: false,
        next_before_seq: "1",
      });
    await client.loadHistory("c");
    expect(client.state.histories.c.map((m) => m.seq)).toEqual([
      "1",
      "50",
      "51",
      "100",
    ]);
    expect(communicationApi.history).toHaveBeenLastCalledWith(
      "c",
      "51",
      expect.any(AbortSignal),
    );
  });
  it("restores focused history after an expired cursor", async () => {
    await boot();
    client.focus("c");
    await client.loadHistory("c");
    vi.mocked(communicationApi.sync).mockRejectedValueOnce(
      new CommunicationError("sync_cursor_expired", 410, true, "expired"),
    );
    await client.synchronize();
    expect(communicationApi.snapshot).toHaveBeenCalledTimes(2);
    expect(client.state.histories.c).toHaveLength(1);
  });
  it("rejects late history results after logout", async () => {
    await boot();
    let finish!: (value: {
      items: CommunicationMessage[];
      has_more: boolean;
      next_before_seq: string;
    }) => void;
    vi.mocked(communicationApi.history).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const task = client.loadHistory("c");
    client.stop();
    auth.token = `header.${btoa(
      JSON.stringify({ sub: 2, sid: "new" }),
    )}.signature`;
    finish({ items: [msg("1")], has_more: false, next_before_seq: "1" });
    await task;
    expect(client.state.histories).toEqual({});
    expect(client.state.contacts).toEqual({});
  });
  it("clears private data on revoked messaging permissions", async () => {
    await boot();
    await client.loadHistory("c");
    vi.mocked(communicationApi.sync).mockRejectedValueOnce(
      new CommunicationError("forbidden", 403, false, "denied"),
    );
    await client.synchronize();
    expect(client.state.phase).toBe("forbidden");
    expect(client.state.histories).toEqual({});
    expect(client.state.conversations).toEqual({});
  });
});

it("replaces the directory and rejects stale history after a department change", async () => {
  await boot();
  let finish!: (value: {
    items: CommunicationMessage[];
    has_more: boolean;
    next_before_seq: string;
  }) => void;
  vi.mocked(communicationApi.history).mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const old = client.loadHistory("c");
  vi.mocked(communicationApi.contacts).mockResolvedValue({
    items: [{ user_id: "new-colleague", nickname: "New", can_message: true }],
  });
  vi.mocked(communicationApi.sync).mockResolvedValue({
    events: [event("contacts.changed", "10", { replace: true })],
    cursor: "cursor10",
    head_seq: "10",
    has_more: false,
  });
  await client.synchronize();
  expect(client.state.contacts[peer]).toBeUndefined();
  expect(client.state.contacts["new-colleague"].nickname).toBe("New");
  finish({ items: [msg("99")], has_more: false, next_before_seq: "99" });
  await old;
  expect(client.state.histories.c).toBeUndefined();
});

describe("communication consistency regressions", () => {
  it.each(["private", "group"] as const)(
    "does not restore cleared %s content from a late send receipt",
    async (type) => {
      await boot();
      client.state.conversations.c = { ...conv, type };
      client.focus("c");
      await client.loadHistory("c");
      let finish!: (receipt: SendReceipt) => void;
      vi.spyOn(communicationApi, "send").mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      );
      const sending = client.send("c", {
        client_message_id: "slow-send",
        text: "already cleared",
        asset_ids: ["private-image"],
      });
      vi.mocked(communicationApi.history).mockResolvedValue({
        items: [],
        has_more: false,
        next_before_seq: "0",
      });
      vi.mocked(communicationApi.sync).mockResolvedValue({
        events: [event("conversation.cleared", "10", { conversation_id: "c" })],
        cursor: "cursor10",
        head_seq: "10",
        has_more: false,
      });
      await client.synchronize();
      expect(client.state.histories.c).toEqual([]);
      finish({
        message_id: "old",
        conversation_id: "c",
        seq: "1",
        created_at: "2026-10-15T10:00:00Z",
      });
      await sending;
      expect(client.state.histories.c).toEqual([]);
      expect(client.state.pending["slow-send"]).toBeUndefined();
    },
  );

  it.each([false, true])(
    "reloads replayed receipts using current visibility (visible=%s)",
    async (visible) => {
      await boot();
      const canonical = {
        ...msg("1"),
        sender_id: own,
        client_message_id: "retry",
      };
      vi.mocked(communicationApi.history).mockResolvedValue({
        items: visible ? [canonical] : [],
        has_more: false,
        next_before_seq: visible ? "1" : "0",
      });
      vi.spyOn(communicationApi, "send").mockResolvedValue({
        message_id: "m1",
        conversation_id: "c",
        seq: "1",
        created_at: canonical.created_at,
        replayed: true,
      });
      await client.send("c", {
        client_message_id: "retry",
        text: "old request content",
      });
      expect(client.state.histories.c).toEqual(visible ? [canonical] : []);
      expect(client.state.pending.retry).toBeUndefined();
    },
  );

  it("does not mark an accepted send as failed when its history refresh fails", async () => {
    await boot();
    vi.spyOn(communicationApi, "send").mockResolvedValue({
      message_id: "m1",
      conversation_id: "c",
      seq: "1",
      created_at: msg("1").created_at,
      replayed: true,
    });
    vi.mocked(communicationApi.history).mockRejectedValue(
      new Error("history offline"),
    );
    await expect(
      client.send("c", { client_message_id: "accepted", text: "sent" }),
    ).resolves.toBeUndefined();
    expect(client.state.pending.accepted).toBeUndefined();
    expect(client.state.histories.c).toBeUndefined();
  });

  it("keeps read acknowledgements monotonic when responses arrive out of order", async () => {
    await boot();
    let first!: (value: { last_read_seq: string }) => void;
    let second!: (value: { last_read_seq: string }) => void;
    vi.spyOn(communicationApi, "read")
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            first = resolve;
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            second = resolve;
          }),
      );
    const older = client.read("c", "9007199254740992");
    const newer = client.read("c", "9007199254740993");
    second({ last_read_seq: "9007199254740993" });
    await newer;
    first({ last_read_seq: "9007199254740992" });
    await older;
    expect(client.state.conversations.c.last_read_seq).toBe("9007199254740993");
  });

  it("preserves newer unread messages when an earlier read request completes", async () => {
    await boot();
    let finish!: (value: { last_read_seq: string }) => void;
    vi.spyOn(communicationApi, "read").mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    const reading = client.read("c", "1");
    vi.mocked(communicationApi.sync).mockResolvedValue({
      events: [event("message.new", "10", { conversation_id: "c" })],
      cursor: "cursor10",
      head_seq: "10",
      has_more: false,
    });
    vi.mocked(communicationApi.conversations).mockResolvedValue({
      items: [{ ...conv, last_seq: "2", last_read_seq: "1", unread: 1 }],
      has_more: false,
    });
    await client.synchronize();
    finish({ last_read_seq: "1" });
    await reading;
    expect(client.state.conversations.c.unread).toBe(1);
    vi.mocked(communicationApi.read).mockResolvedValue({ last_read_seq: "2" });
    await client.read("c", "2");
    expect(client.state.conversations.c.unread).toBe(0);
  });

  it("does not regress read state when older sync metadata follows an acknowledgement", async () => {
    await boot();
    vi.spyOn(communicationApi, "read").mockResolvedValue({
      last_read_seq: "2",
    });
    await client.read("c", "2");
    vi.mocked(communicationApi.sync).mockResolvedValue({
      events: [
        event("conversation.state.changed", "10", {
          conversation_id: "c",
          last_read_seq: "1",
        }),
      ],
      cursor: "cursor10",
      head_seq: "10",
      has_more: false,
    });
    vi.mocked(communicationApi.conversations).mockResolvedValue({
      items: [{ ...conv, last_seq: "2", last_read_seq: "1", unread: 1 }],
      has_more: false,
    });
    await client.synchronize();
    expect(client.state.conversations.c.last_read_seq).toBe("2");
    expect(client.state.conversations.c.unread).toBe(0);
  });

  it("reports sync failures to explicit callers while background callers remain handled", async () => {
    await boot();
    vi.mocked(communicationApi.sync).mockRejectedValueOnce(
      new Error("offline"),
    );
    const background = client.synchronize();
    const foreground = client.synchronize({ throwOnError: true });
    await expect(foreground).rejects.toThrow("offline");
    await expect(background).resolves.toBeUndefined();
    expect(client.state.error).not.toBeNull();
    await expect(
      client.synchronize({ throwOnError: true }),
    ).resolves.toBeUndefined();
    expect(client.state.error).toBeNull();
  });

  it("reports refresh failures after a mutation without running the mutation again", async () => {
    await boot();
    const action = vi.fn().mockResolvedValue({ updated: true });
    vi.mocked(communicationApi.sync).mockRejectedValueOnce(
      new Error("sync offline"),
    );
    await expect(client.mutate(action)).rejects.toThrow("sync offline");
    await client.synchronize({ throwOnError: true });
    expect(action).toHaveBeenCalledTimes(1);
  });
});
