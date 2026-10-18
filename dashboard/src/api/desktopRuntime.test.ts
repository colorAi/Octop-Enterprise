import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  runLocalTurn,
  createRuntimeTaskId,
  type DesktopRuntimeBridge,
  type RuntimeEvent,
} from "./desktopRuntime";
let listener: ((event: RuntimeEvent) => void) | undefined;
let bridge: DesktopRuntimeBridge;
const params = {
  task_id: "task",
  thread_id: "thread",
  agent_id: "expert",
  text: "calculate locally",
};
beforeEach(() => {
  bridge = {
    version: 1,
    status: vi.fn().mockResolvedValue({ active_tasks: [] }),
    start: vi.fn().mockResolvedValue({ task_id: "task" }),
    cancel: vi.fn().mockResolvedValue({ status: "cancel_requested" }),
    reveal: vi.fn(),
    subscribe: vi.fn((fn) => {
      listener = fn;
      return () => {
        listener = undefined;
      };
    }),
  };
  window.__octopRuntime = bridge;
});
afterEach(() => {
  delete window.__octopRuntime;
  listener = undefined;
  vi.restoreAllMocks();
});
it("subscribes before start, filters other threads, and waits for native completion", async () => {
  const events = vi.fn();
  vi.mocked(bridge.start).mockImplementation(async () => {
    listener?.({
      event: "chunk",
      thread_id: "other",
      data: { type: "token", content: "foreign" },
    });
    listener?.({
      event: "chunk",
      thread_id: "thread",
      data: { type: "token", content: "local" },
    });
    listener?.({ event: "done", thread_id: "thread", status: "completed" });
    return { task_id: "task" };
  });
  await runLocalTurn(params, events, new AbortController().signal);
  expect(events).toHaveBeenCalledTimes(2);
  expect(listener).toBeUndefined();
});
it("does not replay or fall back when the worker exits or start fails", async () => {
  const pending = runLocalTurn(params, vi.fn(), new AbortController().signal);
  listener?.({ event: "runtime_exit", error: "结果未知" });
  await expect(pending).rejects.toThrow("结果未知");
  expect(bridge.start).toHaveBeenCalledOnce();
  vi.mocked(bridge.start).mockRejectedValueOnce(new Error("旧会话只读"));
  await expect(
    runLocalTurn(params, vi.fn(), new AbortController().signal),
  ).rejects.toThrow("旧会话只读");
});
it("cancel requests remain pending until the native task terminates", async () => {
  const controller = new AbortController();
  const done = vi.fn();
  const pending = runLocalTurn(params, vi.fn(), controller.signal).then(done);
  controller.abort();
  expect(bridge.cancel).toHaveBeenCalledWith({ thread_id: "thread" });
  await Promise.resolve();
  expect(done).not.toHaveBeenCalled();
  listener?.({ event: "done", thread_id: "thread", status: "cancelled" });
  await pending;
  expect(done).toHaveBeenCalledOnce();
});
it("fails with a specific installation error when the bridge is missing", async () => {
  delete window.__octopRuntime;
  await expect(
    runLocalTurn(params, vi.fn(), new AbortController().signal),
  ).rejects.toThrow("完整企业客户端");
});

it("creates task identities on intranet HTTP without crypto.randomUUID", () => {
  expect(createRuntimeTaskId()).toMatch(
    /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/,
  );
  expect(createRuntimeTaskId()).not.toBe(createRuntimeTaskId());
});
