import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../hooks/useChat";
import { currentTodoProgress } from "./currentTodoProgress";

const step = (id: string, content: string, status = "pending") => ({
  id,
  content,
  status,
});
const user = (id: string): ChatMessage => ({
  id,
  role: "user",
  content: "Task",
  timestamp: 1,
});
function plan(
  id: string,
  items: ReturnType<typeof step>[],
  extra: Partial<ChatMessage> = {},
): ChatMessage {
  return {
    id,
    role: "assistant",
    content: "",
    timestamp: 2,
    status: "done",
    toolData: {
      name: "write_todos",
      arguments: JSON.stringify({ todos: items }),
    },
    ...extra,
  };
}

describe("currentTodoProgress", () => {
  it("keeps the current plan through later replies and tool output", () => {
    const messages = [
      user("request"),
      plan("plan", [
        step("1", "Analyze", "completed"),
        step("2", "Build", "in_progress"),
      ]),
      {
        id: "reply",
        role: "assistant",
        content: "Working",
        timestamp: 3,
      } as ChatMessage,
    ];
    expect(
      currentTodoProgress(messages)?.items.map((item) => item.status),
    ).toEqual(["completed", "in_progress"]);
  });

  it("starts a new turn without showing the old plan, including on history reload", () => {
    const messages = [
      user("old"),
      plan("plan", [step("1", "Old task")]),
      user("new"),
    ];
    expect(currentTodoProgress(messages)).toBeNull();
    expect(currentTodoProgress([])).toBeNull();
  });

  it("uses complete replacement snapshots and respects an explicit empty list", () => {
    const messages = [
      user("request"),
      plan("first", [step("1", "Old"), step("2", "Removed")]),
      plan("replacement", [step("1", "New", "in_progress")]),
    ];
    expect(currentTodoProgress(messages)?.items).toEqual([
      step("1", "New", "in_progress"),
    ]);
    expect(currentTodoProgress([...messages, plan("cleared", [])])).toBeNull();
  });

  it("overlays streamed steps without prematurely dropping the rest of the plan", () => {
    const messages = [
      user("request"),
      plan("first", [step("1", "Analyze"), step("2", "Build")]),
      plan("stream", [], {
        status: "streaming",
        toolData: {
          name: "deepagents/write_todos",
          arguments:
            '{"todos":[{"id":"1","content":"Analyze","status":"completed"},',
        },
      }),
    ];
    expect(currentTodoProgress(messages)?.items).toEqual([
      step("1", "Analyze", "completed"),
      step("2", "Build"),
    ]);
  });

  it("does not replace the host plan with another speaker's or a failed tool call", () => {
    const messages = [
      user("request"),
      plan("host-plan", [step("1", "Host")], { speakerAgentId: "host" }),
      plan("member-plan", [step("1", "Member")], { speakerAgentId: "member" }),
      plan("failed", [step("1", "Failed")], { status: "error" }),
    ];
    expect(currentTodoProgress(messages, "host")?.items).toEqual([
      step("1", "Host"),
    ]);
  });

  it("retains a completed summary and restores plans from a loaded history tail", () => {
    const messages = [plan("plan", [step("1", "Done", "completed")])];
    expect(currentTodoProgress(messages)?.items[0].status).toBe("completed");
  });

  it("keeps the plan while an optimistic steer is pending", () => {
    const messages = [
      user("request"),
      plan("plan", [step("1", "Working")]),
      { ...user("steer"), steerPending: true },
    ];
    expect(currentTodoProgress(messages)?.turnId).toBe("request");
  });
});
