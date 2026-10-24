import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge, RuntimeEvent } from "@/api/desktopRuntime";
import * as store from "./chatStore";
let listener: ((event: RuntimeEvent) => void) | undefined;
let bridge: DesktopRuntimeBridge;
let websocket: ReturnType<typeof vi.fn>;
beforeEach(() => {
  websocket = vi.fn();
  vi.stubGlobal("WebSocket", websocket);
  bridge = {
    version: 1,
    status: vi.fn().mockResolvedValue({ active_tasks: [] }),
    start: vi.fn().mockResolvedValue({ task_id: "task" }),
    cancel: vi.fn().mockResolvedValue({ status: "cancel_requested" }),
    reveal: vi.fn(),
    subscribe: (fn) => {
      listener = fn;
      return () => {
        listener = undefined;
      };
    },
  };
  window.__octopRuntime = bridge;
});
afterEach(() => {
  store.removeSession("local-thread");
  delete window.__octopRuntime;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

it("routes live process output by tool call and replaces the preview with the final result", async () => {
  const pending = store.sendTurn(
    "local-thread",
    "run commands",
    "expert",
    "key",
  );
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: {
      type: "tool_call_chunk",
      id: "exec1",
      index: 0,
      name: "execute",
      args: "{}",
    },
  });
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: {
      type: "tool_call_chunk",
      id: "exec2",
      index: 1,
      name: "execute",
      args: "{}",
    },
  });
  listener?.({
    event: "process_output",
    thread_id: "local-thread",
    tool_call_id: "exec1",
    stream: "stdout",
    content: "working\n",
  });
  const during = store.getSnapshot("local-thread").messages;
  expect(
    during.find((item) => item.toolData?.callId === "exec1")?.toolData?.output,
  ).toBe("working\n");
  expect(
    during.find((item) => item.toolData?.callId === "exec2")?.toolData?.output,
  ).toBeUndefined();
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: {
      type: "tool_result",
      messages: [{ tool_call_id: "exec1", content: "final output" }],
    },
  });
  expect(
    store
      .getSnapshot("local-thread")
      .messages.find((item) => item.toolData?.callId === "exec1")?.toolData
      ?.output,
  ).toBe("final output");
  listener?.({
    event: "done",
    thread_id: "local-thread",
    status: "completed",
    sync_state: "pending",
  });
  await pending;
});
it("passes imported local images with their original hash and size to the native task", async () => {
  const file = {
    path: "C:\\work\\inbound\\photo.png",
    size: 42,
    sha256: "a".repeat(64),
  };
  const attachments = [
    { url: "", filename: "photo.png", kind: "image" as const, localFile: file },
  ];
  store.appendUserMessage("local-thread", {
    id: "draft",
    role: "user",
    content: "Edit this image",
    attachments,
    timestamp: 1,
  });
  const pending = store.sendTurn(
    "local-thread",
    "Edit this image",
    "expert",
    "key",
    attachments,
  );
  expect(bridge.start).toHaveBeenCalledWith(
    expect.objectContaining({
      text: `Edit this image\n\n[file: ${file.path}]`,
      local_attachments: [file],
    }),
  );
  expect(store.getSnapshot("local-thread").messages[0].attachments).toEqual(
    attachments,
  );
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
  expect(websocket).not.toHaveBeenCalled();
});

it("imports uploaded images into the current local project before starting and replaces their previews", async () => {
  const file = {
    path: "C:\\work\\inbound\\uploaded.png",
    size: 42,
    sha256: "b".repeat(64),
  };
  let completeImport!: (value: typeof file) => void;
  bridge.importAttachment = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        completeImport = resolve;
      }),
  );
  const attachments = [
    {
      url: "/api/agents/expert/media/preview?path=inbound%2Fuploaded.png",
      workspacePath: "inbound/uploaded.png",
      filename: "uploaded.png",
      mediaType: "image/png",
      kind: "image" as const,
    },
  ];
  store.appendUserMessage("local-thread", {
    id: "draft",
    role: "user",
    content: "编辑上传的图片",
    attachments,
    timestamp: 1,
  });
  const pending = store.sendTurn(
    "local-thread",
    "编辑上传的图片",
    "expert",
    "key",
    attachments,
  );
  expect(bridge.importAttachment).toHaveBeenCalledWith({
    agent_id: "expert",
    workspace_path: "inbound/uploaded.png",
    filename: "uploaded.png",
    thread_id: "local-thread",
    token: expect.any(String),
  });
  expect(bridge.start).not.toHaveBeenCalled();
  completeImport(file);
  await vi.waitFor(() => expect(bridge.start).toHaveBeenCalledOnce());
  expect(bridge.start).toHaveBeenCalledWith(
    expect.objectContaining({
      text: `编辑上传的图片\n\n[file: ${file.path}]`,
      local_attachments: [file],
    }),
  );
  expect(store.getSnapshot("local-thread").messages[0].attachments).toEqual([
    {
      ...attachments[0],
      url: "",
      workspacePath: file.path,
      localFile: file,
    },
  ]);
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
  expect(websocket).not.toHaveBeenCalled();
});

it("does not duplicate a native file hint already included by forwarding", async () => {
  const file = {
    path: "/work/inbound/image.png",
    size: 42,
    sha256: "a".repeat(64),
  };
  const text = `Edit this image\n\n[file: ${file.path}]`;
  const pending = store.sendTurn("local-thread", text, "expert", "key", [
    { url: "", kind: "image", localFile: file },
  ]);
  expect(bridge.start).toHaveBeenCalledWith(expect.objectContaining({ text }));
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
});

it("retains an imported image when editing and resending its user message", async () => {
  const file = {
    path: "/work/inbound/image.png",
    size: 42,
    sha256: "a".repeat(64),
  };
  const attachments = [{ url: "", kind: "image" as const, localFile: file }];
  vi.mocked(bridge.status).mockResolvedValue({
    active_tasks: [],
    capabilities: ["edit_message"],
  });
  store.appendUserMessage("local-thread", {
    id: "old-user",
    role: "user",
    content: "Old instructions",
    attachments,
    timestamp: 1,
  });
  const pending = store.editLocalMessage(
    "local-thread",
    "old-user",
    "Revised instructions",
    "expert",
  );
  await vi.waitFor(() => expect(bridge.start).toHaveBeenCalledOnce());
  expect(bridge.start).toHaveBeenCalledWith(
    expect.objectContaining({
      text: `Revised instructions\n\n[file: ${file.path}]`,
      edit_message_id: "old-user",
      local_attachments: [file],
    }),
  );
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
  expect(store.getSnapshot("local-thread").messages[0].attachments).toEqual(
    attachments,
  );
});

it("does not start or change a replacement turn when an earlier attachment import returns late", async () => {
  const file = {
    path: "/work/inbound/image.png",
    size: 42,
    sha256: "a".repeat(64),
  };
  let completeImport!: (value: typeof file) => void;
  bridge.importAttachment = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        completeImport = resolve;
      }),
  );
  const first = store.sendTurn("local-thread", "Old request", "expert", "key", [
    { url: "/upload", workspacePath: "inbound/image.png", kind: "image" },
  ]);
  store.appendUserMessage("local-thread", {
    id: "next",
    role: "user",
    content: "New request",
    timestamp: 1,
  });
  const second = store.sendTurn("local-thread", "New request", "expert", "key");
  completeImport(file);
  await first;
  expect(bridge.start).toHaveBeenCalledOnce();
  expect(bridge.start).toHaveBeenCalledWith(
    expect.objectContaining({ text: "New request" }),
  );
  expect(store.getSnapshot("local-thread").messages[0].content).toBe(
    "New request",
  );
  expect(store.getSnapshot("local-thread").isStreaming).toBe(true);
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await second;
});

it("does not start after the user cancels an attachment import", async () => {
  const file = {
    path: "/work/inbound/image.png",
    size: 42,
    sha256: "a".repeat(64),
  };
  let completeImport!: (value: typeof file) => void;
  bridge.importAttachment = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        completeImport = resolve;
      }),
  );
  const pending = store.sendTurn("local-thread", "Edit", "expert", "key", [
    { url: "/upload", workspacePath: "inbound/image.png", kind: "image" },
  ]);
  await expect(store.cancelStream("local-thread", "expert")).resolves.toEqual({
    thread_id: "local-thread",
    requested: true,
  });
  completeImport(file);
  await pending;
  expect(bridge.start).not.toHaveBeenCalled();
  expect(bridge.cancel).not.toHaveBeenCalled();
  expect(store.getSnapshot("local-thread").isStreaming).toBe(false);
  expect(store.getSnapshot("local-thread").messages).toEqual([]);
});

it("still refuses arbitrary URLs without a workspace file instead of downloading them", async () => {
  bridge.importAttachment = vi.fn();
  await store.sendTurn("local-thread", "Edit", "expert", "key", [
    {
      url: "https://example.com/image.png",
      filename: "image.png",
      kind: "image",
    },
  ]);
  expect(bridge.start).not.toHaveBeenCalled();
  expect(bridge.importAttachment).not.toHaveBeenCalled();
  expect(store.getSnapshot("local-thread").messages.at(-1)?.status).toBe(
    "error",
  );
  expect(websocket).not.toHaveBeenCalled();
});

it("inserts into the active native task and waits for its final completion", async () => {
  bridge.steer = vi.fn().mockResolvedValue({
    type: "steer.queued",
    turn_id: "turn1",
    pending_id: "pending1",
    client_pending_id: "draft1",
  });
  const pending = store.sendTurn("local-thread", "original", "expert", "key");
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: { type: "turn.started", turn_id: "turn1" },
  });
  expect(
    store.sendSteerWhileBusy("local-thread", "Use 42", {
      clientPendingId: "draft1",
    }),
  ).toBe("ok");
  await Promise.resolve();
  expect(bridge.steer).toHaveBeenCalledWith({
    thread_id: "local-thread",
    text: "Use 42",
    expected_turn_id: "turn1",
    client_pending_id: "draft1",
  });
  expect(
    store
      .getSnapshot("local-thread")
      .messages.some((m) => m.id === "steer:draft1"),
  ).toBe(true);
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: {
      type: "user",
      content: "Use 42",
      message_id: "canonical",
      meta: { steer: true },
    },
  });
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: { type: "steer.drained", client_pending_ids: ["draft1"] },
  });
  expect(
    store
      .getSnapshot("local-thread")
      .messages.filter((m) => m.content === "Use 42")
      .map((m) => m.id),
  ).toEqual(["canonical"]);
  expect(store.getSnapshot("local-thread").isStreaming).toBe(true);
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
  expect(bridge.start).toHaveBeenCalledOnce();
  expect(bridge.cancel).not.toHaveBeenCalled();
  expect(websocket).not.toHaveBeenCalled();
  expect(store.getActiveTurnId("local-thread")).toBeNull();
});

it("keeps old native clients and attachment inserts out of the new-turn path", async () => {
  const pending = store.sendTurn("local-thread", "original", "expert", "key");
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: { type: "turn.started", turn_id: "turn1" },
  });
  expect(store.sendSteerWhileBusy("local-thread", "correction", {})).toBe(
    "unsupported",
  );
  bridge.steer = vi.fn();
  expect(
    store.sendSteerWhileBusy("local-thread", "file", {
      attachments: [{ url: "/upload", filename: "test.txt", kind: "file" }],
    }),
  ).toBe("attachments");
  expect(bridge.steer).not.toHaveBeenCalled();
  expect(bridge.start).toHaveBeenCalledOnce();
  expect(bridge.cancel).not.toHaveBeenCalled();
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
});

it("restores a native steer only after withdrawal is acknowledged", async () => {
  bridge.steer = vi.fn().mockResolvedValue({
    type: "steer.queued",
    turn_id: "turn1",
    pending_id: "pending1",
    client_pending_id: "draft1",
  });
  let confirm!: (frame: Record<string, unknown>) => void;
  bridge.removeSteer = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        confirm = resolve;
      }),
  );
  const restore = vi.fn();
  const unsubscribe = store.onSteerRestore(restore);
  const pending = store.sendTurn("local-thread", "original", "expert", "key");
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: { type: "turn.started", turn_id: "turn1" },
  });
  store.sendSteerWhileBusy("local-thread", "take it back", {
    clientPendingId: "draft1",
  });
  await Promise.resolve();
  expect(store.withdrawCommittedSteer("local-thread", "steer:draft1")).toBe(
    "ok",
  );
  expect(restore).not.toHaveBeenCalled();
  confirm({
    type: "steer.ack",
    status: "removed",
    client_pending_id: "draft1",
    pending_id: "pending1",
  });
  await Promise.resolve();
  expect(restore).toHaveBeenCalledWith(
    expect.objectContaining({ text: "take it back" }),
  );
  expect(
    store
      .getSnapshot("local-thread")
      .messages.some((m) => m.id === "steer:draft1"),
  ).toBe(false);
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
  unsubscribe();
});
it("does not discard a newer insert when an earlier batch drains", async () => {
  bridge.steer = vi.fn().mockImplementation(async (params) => ({
    type: "steer.queued",
    turn_id: "turn1",
    pending_id: `pending-${params.client_pending_id}`,
    client_pending_id: params.client_pending_id,
  }));
  const pending = store.sendTurn("local-thread", "original", "expert", "key");
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: { type: "turn.started", turn_id: "turn1" },
  });
  store.sendSteerWhileBusy("local-thread", "first", { clientPendingId: "one" });
  store.sendSteerWhileBusy("local-thread", "second", {
    clientPendingId: "two",
  });
  await Promise.resolve();
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: {
      type: "user",
      content: "first",
      message_id: "persisted-one",
      client_pending_ids: ["one"],
      meta: { steer: true },
    },
  });
  listener?.({
    event: "steer",
    thread_id: "local-thread",
    frame: { type: "steer.drained", client_pending_ids: ["one"] },
  });
  expect(
    store
      .getSnapshot("local-thread")
      .messages.filter((m) => m.role === "user")
      .map((m) => m.content),
  ).toEqual(["first", "second"]);
  expect(
    store.getSnapshot("local-thread").messages.find((m) => m.id === "steer:two")
      ?.steerPending,
  ).toBe(true);
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await pending;
});

it("streams company turns locally and keeps server websocket closed", async () => {
  const pending = store.sendTurn(
    "local-thread",
    "hi",
    "expert",
    "key",
    undefined,
    undefined,
    undefined,
    "local-thread",
  );
  expect(bridge.start).toHaveBeenCalledWith(
    expect.objectContaining({
      thread_id: "local-thread",
      agent_id: "expert",
      text: "hi",
    }),
  );
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: { type: "token", content: "local response" },
  });
  listener?.({
    event: "done",
    thread_id: "local-thread",
    status: "completed",
    artifacts: [{ path: "/work/report.xlsx", size: 12, sha256: "abc" }],
  });
  await pending;
  expect(store.getSnapshot("local-thread").isStreaming).toBe(false);
  expect(
    store
      .getSnapshot("local-thread")
      .messages.some((m) => m.content.includes("local response")),
  ).toBe(true);
  expect(
    store
      .getSnapshot("local-thread")
      .messages.some(
        (m) => m.localArtifacts?.[0]?.path === "/work/report.xlsx",
      ),
  ).toBe(true);
  expect(websocket).not.toHaveBeenCalled();
});

it("takes over a cold-start attachment so tokens are consumed only once", async () => {
  const listeners = new Set<(event: RuntimeEvent) => void>();
  bridge.subscribe = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };
  let statusReady!: (value: { active_tasks: [] }) => void;
  bridge.status = vi.fn().mockReturnValue(
    new Promise((resolve) => {
      statusReady = resolve;
    }),
  );
  const attach = store.attachThread("local-thread", "expert", "local-thread");
  const repeated = store.attachThread("local-thread", "expert", "local-thread");
  expect(bridge.status).toHaveBeenCalledOnce();
  const send = store.sendTurn("local-thread", "question", "expert", "");
  expect(listeners.size).toBe(1);
  for (const fn of listeners)
    fn({
      event: "chunk",
      thread_id: "local-thread",
      data: { type: "token", content: "one reply" },
    });
  statusReady({ active_tasks: [] });
  await Promise.all([attach, repeated]);
  expect(store.getSnapshot("local-thread").isStreaming).toBe(true);
  expect(
    store.getSnapshot("local-thread").messages.map((m) => m.content),
  ).toEqual(["one reply"]);
  for (const fn of listeners)
    fn({ event: "done", thread_id: "local-thread", status: "completed" });
  await send;
  expect(listeners.size).toBe(0);
});

it("sends an edit identity and keeps late events from the old task out", async () => {
  bridge.status = vi
    .fn()
    .mockResolvedValue({ active_tasks: [], capabilities: ["edit_message"] });
  store.setMessages("local-thread", [
    { id: "keep", role: "user", content: "earlier", timestamp: 1 },
    { id: "edit-me", role: "user", content: "old", timestamp: 2 },
    { id: "old-answer", role: "assistant", content: "old reply", timestamp: 3 },
  ]);
  const pending = store.editLocalMessage(
    "local-thread",
    "edit-me",
    "revised",
    "expert",
  );
  await vi.waitFor(() => expect(bridge.start).toHaveBeenCalledOnce());
  const params = vi.mocked(bridge.start).mock.calls[0][0];
  expect(params.edit_message_id).toBe("edit-me");
  expect(
    store.getSnapshot("local-thread").messages.map((m) => m.content),
  ).toEqual(["earlier", "revised"]);
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    task_id: "old-task",
    data: { type: "token", content: "stale" },
  });
  listener?.({
    event: "done",
    thread_id: "local-thread",
    task_id: "old-task",
    status: "cancelled",
  });
  expect(store.getSnapshot("local-thread").isStreaming).toBe(true);
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    task_id: params.task_id,
    data: { type: "token", content: "new reply" },
  });
  listener?.({
    event: "done",
    thread_id: "local-thread",
    task_id: params.task_id,
    status: "completed",
  });
  await pending;
  expect(
    store.getSnapshot("local-thread").messages.map((m) => m.content),
  ).toEqual(["earlier", "revised", "new reply"]);
});

it("waits for cancellation completion and blocks a second edit submission", async () => {
  const listeners = new Set<(event: RuntimeEvent) => void>();
  bridge.subscribe = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };
  store.appendUserMessage("local-thread", {
    id: "initial",
    role: "user",
    content: "old",
    timestamp: 1,
  });
  const first = store.sendTurn("local-thread", "old", "expert", "");
  const oldTask = vi.mocked(bridge.start).mock.calls[0][0].task_id;
  bridge.status = vi.fn().mockResolvedValue({
    active_tasks: [
      { task_id: oldTask, thread_id: "local-thread", status: "running" },
    ],
    capabilities: ["edit_message"],
  });
  const messageId = store.getSnapshot("local-thread").messages[0].id;
  const edit = store.editLocalMessage(
    "local-thread",
    messageId,
    "revised",
    "expert",
  );
  await vi.waitFor(() => expect(bridge.cancel).toHaveBeenCalledOnce());
  await store.editLocalMessage(
    "local-thread",
    messageId,
    "duplicate",
    "expert",
  );
  expect(bridge.start).toHaveBeenCalledOnce();
  for (const fn of [...listeners])
    fn({
      event: "done",
      thread_id: "local-thread",
      task_id: oldTask,
      status: "cancelled",
    });
  await first;
  await vi.waitFor(() => expect(bridge.start).toHaveBeenCalledTimes(2));
  const nextTask = vi.mocked(bridge.start).mock.calls[1][0].task_id;
  for (const fn of [...listeners])
    fn({
      event: "done",
      thread_id: "local-thread",
      task_id: nextTask,
      status: "completed",
    });
  await edit;
  expect(
    store
      .getSnapshot("local-thread")
      .messages.filter((m) => m.role === "user")
      .map((m) => m.content),
  ).toEqual(["revised"]);
});

it("preserves history when the client cannot edit or the start is rejected", async () => {
  const original = [
    { id: "original", role: "user" as const, content: "keep", timestamp: 1 },
  ];
  store.setMessages("local-thread", original);
  await expect(
    store.editLocalMessage("local-thread", "original", "changed", "expert"),
  ).rejects.toThrow("新版");
  expect(store.getSnapshot("local-thread").messages).toEqual(original);
  expect(bridge.start).not.toHaveBeenCalled();
  bridge.status = vi
    .fn()
    .mockResolvedValue({ active_tasks: [], capabilities: ["edit_message"] });
  bridge.start = vi.fn().mockRejectedValue(new Error("edit_history_changed"));
  await store.editLocalMessage("local-thread", "original", "changed", "expert");
  expect(store.getSnapshot("local-thread").messages[0]).toEqual(original[0]);
  expect(store.getSnapshot("local-thread").isStreaming).toBe(false);
});

it("allows a further correction after an edit has started, while preventing duplicate cancellation submits", async () => {
  const listeners = new Set<(event: RuntimeEvent) => void>();
  bridge.subscribe = (fn) => {
    listeners.add(fn);
    return () => listeners.delete(fn);
  };
  bridge.status = vi
    .fn()
    .mockResolvedValue({ active_tasks: [], capabilities: ["edit_message"] });
  store.setMessages("local-thread", [
    { id: "original", role: "user", content: "old", timestamp: 1 },
  ]);
  const first = store.editLocalMessage(
    "local-thread",
    "original",
    "first edit",
    "expert",
  );
  await vi.waitFor(() => expect(bridge.start).toHaveBeenCalledOnce());
  const task = vi.mocked(bridge.start).mock.calls[0][0].task_id;
  const messageId = store.getSnapshot("local-thread").messages[0].id;
  bridge.status = vi.fn().mockResolvedValue({
    active_tasks: [
      { task_id: task, thread_id: "local-thread", status: "running" },
    ],
    capabilities: ["edit_message"],
  });
  const second = store.editLocalMessage(
    "local-thread",
    messageId,
    "second edit",
    "expert",
  );
  await vi.waitFor(() => expect(bridge.cancel).toHaveBeenCalledOnce());
  await store.editLocalMessage(
    "local-thread",
    messageId,
    "duplicate",
    "expert",
  );
  expect(bridge.start).toHaveBeenCalledOnce();
  for (const fn of [...listeners])
    fn({
      event: "done",
      thread_id: "local-thread",
      task_id: task,
      status: "cancelled",
    });
  await first;
  await vi.waitFor(() => expect(bridge.start).toHaveBeenCalledTimes(2));
  const next = vi.mocked(bridge.start).mock.calls[1][0].task_id;
  for (const fn of [...listeners])
    fn({
      event: "done",
      thread_id: "local-thread",
      task_id: next,
      status: "completed",
    });
  await second;
  expect(
    store.getSnapshot("local-thread").messages.map((m) => m.content),
  ).toEqual(["second edit"]);
});
it("native errors and history attachment never fall back to server execution", async () => {
  vi.mocked(bridge.start).mockRejectedValueOnce(
    new Error("请先选择本地工作目录"),
  );
  await store.sendTurn("local-thread", "hi", "expert", "key");
  await store.attachThread("local-thread", "expert", "local-thread");
  expect(store.getSnapshot("local-thread").messages.at(-1)?.content).toBe(
    "请先选择本地工作目录",
  );
  expect(websocket).not.toHaveBeenCalled();
});
it("cancels through native IPC and does not call server cancellation", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  await expect(store.cancelStream("local-thread", "expert")).resolves.toEqual({
    thread_id: "local-thread",
    requested: true,
  });
  expect(bridge.cancel).toHaveBeenCalledWith({ thread_id: "local-thread" });
  expect(fetch).not.toHaveBeenCalled();
});

it("reattaches to a native task after a page reload without replaying start", async () => {
  vi.mocked(bridge.status).mockResolvedValue({
    active_tasks: [
      { task_id: "task", thread_id: "local-thread", status: "running" },
    ],
  });
  await store.attachThread("local-thread", "expert", "local-thread");
  expect(store.getSnapshot("local-thread").isStreaming).toBe(true);
  expect(bridge.start).not.toHaveBeenCalled();
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: { type: "token", content: "continued" },
  });
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  expect(store.getSnapshot("local-thread").isStreaming).toBe(false);
  expect(websocket).not.toHaveBeenCalled();
});

it("does not resume a legacy server approval from a company desktop", async () => {
  const fetch = vi.fn();
  vi.stubGlobal("fetch", fetch);
  await store.resumeHitl("local-thread", "expert", "local-thread", [
    { type: "approve" },
  ]);
  expect(fetch).not.toHaveBeenCalled();
  expect(store.getSnapshot("local-thread").messages.at(-1)?.content).toContain(
    "仅供查看",
  );
});

it("shows a native approval, resumes through IPC, and retains the live subscription", async () => {
  bridge.resumeHitl = vi.fn().mockResolvedValue({ status: "resuming" });
  const turn = store.sendTurn(
    "local-thread",
    "remove fixture",
    "expert",
    "key",
  );
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: {
      type: "hitl_required",
      request: {
        pending_id: "local-one",
        action_requests: [{ name: "execute", args: { command: "rm fixture" } }],
      },
    },
  });
  expect(store.getSnapshot("local-thread").isStreaming).toBe(false);
  expect(
    store.getSnapshot("local-thread").messages.at(-1)?.hitlData?.pending_id,
  ).toBe("local-one");
  await store.resumeHitl(
    "local-thread",
    "expert",
    "local-thread",
    [{ type: "approve" }],
    undefined,
    false,
    undefined,
    "local-one",
  );
  expect(bridge.resumeHitl).toHaveBeenCalledWith({
    thread_id: "local-thread",
    pending_id: "local-one",
    decisions: [{ type: "approve" }],
  });
  expect(store.getSnapshot("local-thread").isStreaming).toBe(true);
  expect(bridge.start).toHaveBeenCalledTimes(1);
  expect(bridge.cancel).not.toHaveBeenCalled();
  listener?.({ event: "done", thread_id: "local-thread", status: "completed" });
  await turn;
  expect(store.getSnapshot("local-thread").isStreaming).toBe(false);
});

it("restores the pending native approval on a page reattach", async () => {
  vi.mocked(bridge.status).mockResolvedValue({
    active_tasks: [
      {
        task_id: "paused-task",
        thread_id: "local-thread",
        status: "running",
        pending_approval: {
          pending_id: "local-pending",
          action_requests: [
            { name: "execute", args: { command: "rm fixture" } },
          ],
        },
      },
    ],
  });
  await store.attachThread("local-thread", "expert", "local-thread");
  expect(store.getSnapshot("local-thread").isStreaming).toBe(false);
  expect(
    store.getSnapshot("local-thread").messages.at(-1)?.hitlData?.pending_id,
  ).toBe("local-pending");
  expect(bridge.start).not.toHaveBeenCalled();
  listener?.({ event: "done", thread_id: "local-thread", status: "cancelled" });
});

it("keeps two live image turns separate even with an old cumulative native result", async () => {
  const first = { path: "/work/one.png", size: 1, sha256: "a".repeat(64) };
  const second = { path: "/work/two.png", size: 2, sha256: "b".repeat(64) };
  store.appendUserMessage("local-thread", {
    id: "optimistic-1",
    role: "user",
    content: "first",
    timestamp: 1,
  });
  const run1 = store.sendTurn("local-thread", "first", "expert", "key");
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: { type: "token", content: "first image" },
  });
  listener?.({
    event: "done",
    thread_id: "local-thread",
    status: "completed",
    artifacts: [first],
  });
  await run1;
  const firstReceipt = store
    .getSnapshot("local-thread")
    .messages.find((m) => m.localArtifacts);
  store.appendUserMessage("local-thread", {
    id: "optimistic-2",
    role: "user",
    content: "second",
    timestamp: 3,
  });
  const run2 = store.sendTurn("local-thread", "second", "expert", "key");
  listener?.({
    event: "chunk",
    thread_id: "local-thread",
    data: { type: "token", content: "second image" },
  });
  listener?.({
    event: "done",
    thread_id: "local-thread",
    status: "completed",
    artifacts: [first, second],
  });
  await run2;
  const state = store.getSnapshot("local-thread").messages;
  const receipts = state.filter((m) => m.localArtifacts);
  expect(receipts.map((m) => m.localArtifacts)).toEqual([[first], [second]]);
  expect(receipts[0]).toEqual(firstReceipt);
  const requests = vi.mocked(bridge.start).mock.calls.map((call) => call[0]);
  expect(state.filter((m) => m.role === "user").map((m) => m.id)).toEqual(
    requests.map((r) => `${r.task_id}_user`),
  );
  expect(receipts.map((m) => m.id)).toEqual(
    requests.map((r) => `${r.task_id}:local-artifacts`),
  );
  expect(state.indexOf(receipts[0])).toBeLessThan(
    state.findIndex((m) => m.id === `${requests[1].task_id}_user`),
  );
});

it("does not reattach an older off-page image from a cumulative native result", async () => {
  const old = { path: "/work/not-loaded.png", size: 1, sha256: "a".repeat(64) };
  const fresh = { path: "/work/new.png", size: 1, sha256: "b".repeat(64) };
  store.rememberLocalArtifactHistory("local-thread", [old]);
  store.appendUserMessage("local-thread", {
    id: "optimistic",
    role: "user",
    content: "new",
    timestamp: 1,
  });
  const pending = store.sendTurn("local-thread", "new", "expert", "key");
  listener?.({
    event: "done",
    thread_id: "local-thread",
    status: "completed",
    artifacts: [old, fresh],
  });
  await pending;
  expect(
    store
      .getSnapshot("local-thread")
      .messages.filter((m) => m.localArtifacts)
      .map((m) => m.localArtifacts),
  ).toEqual([[fresh]]);
});

it("displays an explicitly resent image even when its hash is already in history", async () => {
  const file = { path: "/work/old.png", size: 1, sha256: "a".repeat(64) };
  store.rememberLocalArtifactHistory("local-thread", [file]);
  store.appendUserMessage("local-thread", {
    id: "optimistic",
    role: "user",
    content: "把图直接发给我",
    timestamp: 1,
  });
  const pending = store.sendTurn(
    "local-thread",
    "把图直接发给我",
    "expert",
    "key",
  );
  const sent = { ...file, explicitly_sent: true };
  listener?.({
    event: "done",
    thread_id: "local-thread",
    status: "completed",
    artifacts: [sent],
  });
  await pending;
  expect(
    store.getSnapshot("local-thread").messages.at(-1)?.localArtifacts,
  ).toEqual([sent]);
});
