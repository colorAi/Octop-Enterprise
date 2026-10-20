import { describe, expect, it } from "vitest";
import type { LocalArtifactTurn } from "@/api/desktopRuntime";
import type { ChatMessage } from "../hooks/useChat";
import {
  changedLocalArtifacts,
  placeLocalArtifactTurns,
} from "./localArtifactTurns";
import { groupConsecutiveAssistantMessages } from "./messageGrouping";
const one = { path: "/work/one.png", size: 1, sha256: "a".repeat(64) };
const two = { path: "/work/two.png", size: 2, sha256: "b".repeat(64) };
const turns: LocalArtifactTurn[] = [
  {
    task_id: "t1",
    user_message_id: "t1_user",
    message_id: "a1",
    device_id: "pc",
    files: [one],
  },
  {
    task_id: "t2",
    user_message_id: "t2_user",
    message_id: "a2",
    device_id: "pc",
    files: [two],
  },
];
const messages: ChatMessage[] = [
  { id: "t1_user", role: "user", content: "first", timestamp: 1 },
  { id: "a1", role: "assistant", content: "first image", timestamp: 2 },
  { id: "t2_user", role: "user", content: "second", timestamp: 3 },
  { id: "a2", role: "assistant", content: "second image", timestamp: 4 },
];
describe("per-turn local artifacts", () => {
  it("keeps each image in its own assistant group through repeat history loads", () => {
    const result = placeLocalArtifactTurns(messages, turns);
    expect(result.map((m) => m.id)).toEqual([
      "t1_user",
      "a1",
      "t1:local-artifacts",
      "t2_user",
      "a2",
      "t2:local-artifacts",
    ]);
    const groups = groupConsecutiveAssistantMessages(result).filter(
      (g) => g.isGroup,
    );
    expect(
      groups.map((g) => g.messages.flatMap((m) => m.localArtifacts ?? [])),
    ).toEqual([[one], [two]]);
    expect(placeLocalArtifactTurns(result, turns)).toEqual(result);
  });
  it("uses the user boundary even when an old model reused an assistant message id", () => {
    const repeated = messages.map((message) =>
      message.role === "assistant" ? { ...message, id: "same-ai-id" } : message,
    );
    const result = placeLocalArtifactTurns(
      repeated,
      turns.map((turn) => ({ ...turn, message_id: "same-ai-id" })),
    );
    expect(result.map((message) => message.id)).toEqual([
      "t1_user",
      "same-ai-id",
      "t1:local-artifacts",
      "t2_user",
      "same-ai-id",
      "t2:local-artifacts",
    ]);
  });
  it("does not move an off-page turn onto a different page", () => {
    expect(
      placeLocalArtifactTurns(messages.slice(2), turns).map((m) => m.id),
    ).toEqual(["t2_user", "a2", "t2:local-artifacts"]);
    expect(
      placeLocalArtifactTurns(messages.slice(0, 2), turns).map((m) => m.id),
    ).toEqual(["t1_user", "a1", "t1:local-artifacts"]);
    expect(placeLocalArtifactTurns([], turns)).toEqual([]);
  });
  it("can anchor a page split inside a turn using either its AI or user id", () => {
    expect(
      placeLocalArtifactTurns([messages[1]], turns).at(-1)?.localArtifacts,
    ).toEqual([one]);
    expect(
      placeLocalArtifactTurns([messages[0]], turns).at(-1)?.localArtifacts,
    ).toEqual([one]);
  });
  it("retains the original receipt when a later turn overwrites the same path", () => {
    const changed = { ...one, sha256: "c".repeat(64) };
    const result = placeLocalArtifactTurns(messages, [
      turns[0],
      { ...turns[1], files: [changed] },
    ]);
    expect(
      result.filter((m) => m.localArtifacts).map((m) => m.localArtifacts),
    ).toEqual([[one], [changed]]);
    expect(changedLocalArtifacts(result, [changed, two])).toEqual([two]);
    expect(changedLocalArtifacts(result, [one])).toEqual([one]);
  });
  it("filters the previous image out of an old client's cumulative done event", () => {
    const result = placeLocalArtifactTurns(messages.slice(0, 2), [turns[0]]);
    expect(changedLocalArtifacts(result, [one, two])).toEqual([two]);
  });
  it("shows an explicitly resent image again without moving its earlier receipt", () => {
    const first = placeLocalArtifactTurns(messages, [turns[0]]);
    const resent = { ...one, explicitly_sent: true };
    const files = changedLocalArtifacts(first, [resent], [one]);
    expect(files).toEqual([resent]);
    const result = placeLocalArtifactTurns(first, [{ ...turns[1], files }]);
    expect(
      result.filter((m) => m.localArtifacts).map((m) => m.localArtifacts),
    ).toEqual([[one], [resent]]);
    expect(
      placeLocalArtifactTurns(messages, [turns[0], { ...turns[1], files }]),
    ).toEqual(result);
  });
});
