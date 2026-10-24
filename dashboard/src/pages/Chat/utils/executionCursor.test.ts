import { describe, expect, it } from "vitest";
import { acceptExecutionFrame, executionCursor } from "./executionCursor";

describe("durable execution cursors", () => {
  it("replays remaining frames within a partially received batch once", () => {
    const frame = { execution_id: "run", event_seq: 3, event_index: 0 };
    expect(acceptExecutionFrame("thread", frame)).toBe(true);
    expect(acceptExecutionFrame("thread", frame)).toBe(false);
    expect(acceptExecutionFrame("thread", { ...frame, event_index: 1 })).toBe(
      true,
    );
    expect(executionCursor("thread")).toEqual({ ...frame, event_index: 1 });
    expect(acceptExecutionFrame("thread", { ...frame, event_seq: 2 })).toBe(
      false,
    );
    expect(
      acceptExecutionFrame("thread", {
        ...frame,
        execution_id: "next",
        event_seq: 1,
      }),
    ).toBe(true);
  });
  it("isolates threads and preserves legacy frames", () => {
    expect(executionCursor("other")).toEqual({});
    expect(
      acceptExecutionFrame("other", { type: "token", text: "legacy" }),
    ).toBe(true);
  });
});
