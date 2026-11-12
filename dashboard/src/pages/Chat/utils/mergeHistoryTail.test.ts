import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../hooks/sseHelpers";
import { mergeHistoryWithLocalTail } from "./mergeHistoryTail";

function msg(
  id: string,
  role: ChatMessage["role"],
  content: string,
  timestamp = 1,
): ChatMessage {
  return {
    id,
    role,
    content,
    timestamp,
    ...(role === "user" && id.startsWith("local-")
      ? { localPending: true }
      : {}),
  };
}

describe("mergeHistoryWithLocalTail", () => {
  it("drops old non-overlapping history instead of appending it after the latest page", () => {
    const loaded = [
      msg("new-u", "user", "new", 9000),
      msg("new-a", "assistant", "new answer", 9500),
    ];
    const old = [
      msg("old-u", "user", "old", 1000),
      msg("old-a", "assistant", "old answer", 1500),
    ];
    expect(mergeHistoryWithLocalTail(loaded, old)).toEqual(loaded);
    expect(mergeHistoryWithLocalTail([], old)).toEqual([]);
  });

  it("does not retain a stale user tail just because an earlier message overlaps", () => {
    const loaded = [
      msg("anchor", "assistant", "kept"),
      msg("new", "user", "new"),
    ];
    const old = [
      loaded[0],
      msg("stale", "user", "removed branch"),
      msg("stale-answer", "assistant", "old"),
    ];
    expect(mergeHistoryWithLocalTail(loaded, old)).toEqual(loaded);
  });

  it("retains every pending send but drops the older unrelated cached prefix", () => {
    const loaded = [msg("new", "assistant", "server")];
    const current = [
      msg("old", "user", "stale"),
      msg("local-first", "user", "first"),
      msg("reply", "assistant", "reply"),
      msg("local-second", "user", "second"),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current)).toEqual([
      loaded[0],
      ...current.slice(1),
    ]);
    expect(mergeHistoryWithLocalTail([], current)).toEqual(current.slice(1));
  });

  it("keeps a fuller live reply but accepts the authoritative completed reply", () => {
    const user = msg("u", "user", "question");
    const current = [user, msg("local-a", "assistant", "long live answer")];
    const loaded = [user, msg("stored-a", "assistant", "short")];
    expect(mergeHistoryWithLocalTail(loaded, current, true)).toEqual(current);
    expect(mergeHistoryWithLocalTail(loaded, current, false)).toEqual(loaded);
  });

  it("does not let an old live reply hide a newer persisted user turn", () => {
    const user = msg("u", "user", "question");
    const current = [
      user,
      {
        ...msg("local-a", "assistant", "long old reply"),
        status: "streaming" as const,
      },
    ];
    const loaded = [
      user,
      msg("answer", "assistant", "done"),
      msg("later", "user", "next"),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current, true)).toEqual(loaded);
  });

  it.each([false, true])(
    "preserves a live answer with server-assigned user ids (shared anchor: %s)",
    (sharedAnchor) => {
      const anchor = sharedAnchor
        ? [msg("anchor", "assistant", "earlier")]
        : [];
      const loaded = [
        ...anchor,
        msg("server-u", "user", "question", 5000),
        msg("server-a", "assistant", "part"),
      ];
      const current = [
        ...anchor,
        msg("local-u", "user", "question", 5000),
        msg("local-a", "assistant", "complete live answer"),
      ];
      expect(mergeHistoryWithLocalTail(loaded, current, true)).toEqual([
        ...loaded.slice(0, -1),
        current[current.length - 1],
      ]);
      expect(mergeHistoryWithLocalTail(loaded, current, false)).toEqual(loaded);
    },
  );

  it("keeps a user message sent while the server page was in flight", () => {
    const loaded = [msg("old-a", "assistant", "之前的回复", 1_000)];
    const current = [msg("local-user", "user", "继续做", 5_000)];
    expect(
      mergeHistoryWithLocalTail(loaded, current).map((m) => m.content),
    ).toEqual(["之前的回复", "继续做"]);
  });

  it("reconciles several completed local turns with server-assigned ids", () => {
    const loaded = [
      msg("server-u1", "user", "first", 1000),
      msg("server-a1", "assistant", "first answer"),
      msg("server-u2", "user", "second", 5000),
      msg("server-a2", "assistant", "second answer"),
    ];
    const current = [
      msg("local-u1", "user", "first", 1000),
      msg("local-a1", "assistant", "first answer"),
      msg("local-u2", "user", "second", 5000),
      msg("local-a2", "assistant", "longer second live answer"),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current)).toEqual(loaded);
    expect(mergeHistoryWithLocalTail(loaded, current, true)).toEqual([
      ...loaded.slice(0, -1),
      current[3],
    ]);
  });

  it("only appends sends after the last turn acknowledged under a server id", () => {
    const loaded = [
      msg("server-u1", "user", "first", 1000),
      msg("server-a1", "assistant", "first answer"),
    ];
    const current = [
      msg("local-u1", "user", "first", 1000),
      msg("local-a1", "assistant", "first answer"),
      msg("local-u2", "user", "second", 5000),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current)).toEqual([
      ...loaded,
      current[2],
    ]);
  });

  it("keeps the in-flight assistant bubbles that follow that user message", () => {
    const loaded = [msg("old-a", "assistant", "之前的回复", 1_000)];
    const current = [
      msg("local-user", "user", "继续做", 5_000),
      msg("local-a", "assistant", "正在处理", 5_100),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current).map((m) => m.id)).toEqual(
      ["old-a", "local-user", "local-a"],
    );
  });

  it.each([false, true])(
    "puts a stored answer after its pending question (shared anchor: %s)",
    (sharedAnchor) => {
      const anchor = msg("anchor", "assistant", "previous answer", 1_000);
      const older = msg("older", "assistant", "earlier history", 2_000);
      const question = msg("local-user", "user", "question", 5_000);
      const preamble = msg("local-a", "assistant", "working", 5_100);
      const answer = msg("stored-a", "assistant", "answer", 5_200);
      const loaded = sharedAnchor ? [anchor, older, answer] : [answer];
      const current = sharedAnchor
        ? [anchor, question, preamble]
        : [question, preamble];

      expect(mergeHistoryWithLocalTail(loaded, current, true)).toEqual(
        sharedAnchor
          ? [anchor, older, question, preamble, answer]
          : [question, preamble, answer],
      );
    },
  );

  it("keeps a stored answer with its question when a later send is also pending", () => {
    const older = msg("older", "assistant", "earlier history", 1_000);
    const first = msg("local-first", "user", "first question", 5_000);
    const answer = msg("stored-a", "assistant", "first answer", 5_200);
    const second = msg("local-second", "user", "second question", 6_000);

    expect(mergeHistoryWithLocalTail([older, answer], [first, second])).toEqual(
      [older, first, answer, second],
    );
  });

  it("keeps the pending question when the same incomplete page is refreshed again", () => {
    const anchor = msg("anchor", "assistant", "previous answer", 1_000);
    const question = msg("local-user", "user", "question", 5_000);
    const answer = msg("stored-a", "assistant", "answer", 5_200);
    const loaded = [anchor, answer];
    const current = [anchor, question, answer];

    expect(mergeHistoryWithLocalTail(loaded, current)).toEqual(current);
  });

  it("preserves timestamp-less rows on a page that includes earlier history", () => {
    const older = msg("older", "assistant", "earlier history", 1_000);
    const untimed = msg("untimed", "assistant", "earlier answer", 0);
    const question = msg("local-user", "user", "question", 5_000);
    const answer = msg("stored-a", "assistant", "answer", 5_200);

    expect(
      mergeHistoryWithLocalTail([older, untimed, answer], [question]),
    ).toEqual([older, untimed, question, answer]);
  });

  it("does not duplicate a turn the server already stored under new ids", () => {
    const loaded = [
      msg("old-a", "assistant", "之前的回复", 1_000),
      msg("srv-user", "user", "继续做", 5_000),
      msg("srv-a", "assistant", "做好了", 5_200),
    ];
    const current = [
      msg("local-user", "user", "继续做", 5_000),
      msg("local-a", "assistant", "做好了", 5_100),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current).map((m) => m.id)).toEqual(
      ["old-a", "srv-user", "srv-a"],
    );
  });

  it("keeps a second send of the same text", () => {
    const loaded = [
      msg("u1", "user", "继续做", 1_000),
      msg("a1", "assistant", "第一次", 1_100),
    ];
    const current = [
      msg("u1", "user", "继续做", 1_000),
      msg("a1", "assistant", "第一次", 1_100),
      msg("local-user", "user", "继续做", 9_000),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current).map((m) => m.id)).toEqual(
      ["u1", "a1", "local-user"],
    );
  });

  it("keeps older pages already prepended above the latest server page", () => {
    const loaded = [msg("m2", "user", "第二页", 2_000)];
    const current = [
      msg("m1", "user", "更早", 1_000),
      msg("m2", "user", "第二页", 2_000),
      msg("local-user", "user", "新的", 8_000),
    ];
    expect(mergeHistoryWithLocalTail(loaded, current).map((m) => m.id)).toEqual(
      ["m1", "m2", "local-user"],
    );
  });
});
