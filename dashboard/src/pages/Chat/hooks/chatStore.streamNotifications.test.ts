import { afterEach, beforeEach, expect, it, vi } from "vitest";
import * as store from "./chatStore";

const SESSION = "stream-notifications";
let frames: Map<number, FrameRequestCallback>;
let nextFrame: number;

beforeEach(() => {
  frames = new Map();
  nextFrame = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.set(++nextFrame, callback);
    return nextFrame;
  });
  vi.stubGlobal("cancelAnimationFrame", (frame: number) =>
    frames.delete(frame),
  );
});

afterEach(() => {
  store.removeSession(SESSION);
  vi.unstubAllGlobals();
});

function flushFrame(): void {
  const callbacks = [...frames.values()];
  frames.clear();
  callbacks.forEach((callback) => callback(0));
}

it("delivers a burst once per frame without losing any text or stale synchronous reads", () => {
  const listener = vi.fn();
  const unsubscribe = store.subscribe(SESSION, listener);
  const deltas = Array.from({ length: 1000 }, (_, index) => `[${index}]`);
  for (const content of deltas)
    store.ingestHarnessChunk(SESSION, { type: "token", content });
  expect(listener).not.toHaveBeenCalled();
  expect(frames.size).toBe(1);
  expect(store.getSnapshot(SESSION).messages.at(-1)?.content).toBe(
    deltas.join(""),
  );
  flushFrame();
  expect(listener).toHaveBeenCalledTimes(1);
  store.ingestHarnessChunk(SESSION, { type: "token", content: "y" });
  flushFrame();
  expect(listener).toHaveBeenCalledTimes(2);
  expect(store.getSnapshot(SESSION).messages.at(-1)?.content).toBe(
    deltas.join("") + "y",
  );
  unsubscribe();
});

it("flushes completion immediately and cancels a queued frame", () => {
  const listener = vi.fn();
  const unsubscribe = store.subscribe(SESSION, listener);
  store.ingestHarnessChunk(SESSION, { type: "token", content: "complete" });
  store.ingestHarnessChunk(SESSION, { type: "done" });
  expect(listener).toHaveBeenCalledTimes(1);
  expect(store.getSnapshot(SESSION).messages.at(-1)?.status).toBe("done");
  expect(frames.size).toBe(0);
  flushFrame();
  expect(listener).toHaveBeenCalledTimes(1);
  unsubscribe();
});

it("does not schedule unobserved sessions and cancels work on removal", () => {
  store.ingestHarnessChunk(SESSION, { type: "token", content: "background" });
  expect(frames.size).toBe(0);
  const listener = vi.fn();
  store.subscribe(SESSION, listener);
  expect(frames.size).toBe(1);
  expect(store.getSnapshot(SESSION).messages.at(-1)?.content).toBe(
    "background",
  );
  store.removeSession(SESSION);
  expect(frames.size).toBe(0);
  expect(listener).not.toHaveBeenCalled();
});

it("ignores graph updates that carry no context change", () => {
  const listener = vi.fn();
  const unsubscribe = store.subscribe(SESSION, listener);
  const chunk = {
    type: "state_snapshot" as const,
    data: {
      messages: [{ type: "ai", usage_metadata: { input_tokens: 12000 } }],
    },
  };
  store.ingestHarnessChunk(SESSION, chunk);
  flushFrame();
  store.ingestHarnessChunk(SESSION, chunk);
  store.ingestHarnessChunk(SESSION, {
    type: "custom",
    data: { debug: "ignored" },
  });
  expect(frames.size).toBe(0);
  expect(listener).toHaveBeenCalledTimes(1);
  unsubscribe();
});
