import { afterEach, expect, it, vi } from "vitest";
import {
  createDesktopProjectThread,
  setDesktopProjectSnapshot,
  type DesktopProjectState,
} from "./desktopProjects";
import type { DesktopRuntimeBridge } from "./desktopRuntime";
import { octopThreadsApi } from "./modules/octopThreads";
import { request } from "./request";
vi.mock("./request", () => ({ request: vi.fn() }));
const state = (id: string): DesktopProjectState => ({
  supported: true,
  current_project_id: id,
  projects: {},
  threads: {},
});
afterEach(() => {
  delete window.__octopRuntime;
  setDesktopProjectSnapshot(null);
  vi.clearAllMocks();
});
it("captures the selected project before an async create and uses the chosen expert", async () => {
  const create = vi.fn().mockResolvedValue({
    thread_id: "created",
    project_id: "A",
    agent_id: "writer",
  });
  window.__octopRuntime = {
    version: 1,
    projects: { create },
  } as unknown as DesktopRuntimeBridge;
  setDesktopProjectSnapshot(state("A"));
  const pending = octopThreadsApi.create("writer");
  setDesktopProjectSnapshot(state("B"));
  await pending;
  expect(create).toHaveBeenCalledWith(
    expect.objectContaining({
      project_id: "A",
      agent_id: "writer",
      thread_id: expect.stringMatching(/^thr_/),
    }),
  );
  expect(request).not.toHaveBeenCalled();
});
it("never falls back to a server folder after a native project failure", async () => {
  window.__octopRuntime = {
    projects: {
      create: vi.fn().mockRejectedValue(new Error("project_directory_changed")),
    },
  } as unknown as DesktopRuntimeBridge;
  setDesktopProjectSnapshot(state("A"));
  await expect(octopThreadsApi.create("writer")).rejects.toThrow(
    "project_directory_changed",
  );
  expect(request).not.toHaveBeenCalled();
});
it("requires a project before creating a conversation", async () => {
  window.__octopRuntime = { projects: {} } as unknown as DesktopRuntimeBridge;
  setDesktopProjectSnapshot(state(""));
  await expect(createDesktopProjectThread("writer")).rejects.toThrow(
    "选择项目文件夹",
  );
  expect(request).not.toHaveBeenCalled();
});
it("retains the negotiated old-server workflow", async () => {
  window.__octopRuntime = {
    projects: { status: vi.fn().mockResolvedValue({ supported: false }) },
  } as unknown as DesktopRuntimeBridge;
  vi.mocked(request).mockResolvedValue({
    thread_id: "legacy",
    session_key: "key",
  });
  expect((await octopThreadsApi.create("writer")).thread_id).toBe("legacy");
  expect(request).toHaveBeenCalledTimes(1);
});
it("forks the source conversation even when another project is selected", async () => {
  const fork = vi.fn().mockResolvedValue({
    thread_id: "fork",
    project_id: "A",
    agent_id: "writer",
    copied_messages: 4,
  });
  window.__octopRuntime = {
    projects: { status: vi.fn().mockResolvedValue(state("B")), fork },
  } as unknown as DesktopRuntimeBridge;
  expect(
    (
      await octopThreadsApi.fork("writer", "source-a", {
        assistant_turns_from_end: 1,
      })
    ).thread_id,
  ).toBe("fork");
  expect(fork).toHaveBeenCalledWith(
    expect.objectContaining({
      source_thread_id: "source-a",
      assistant_turns_from_end: 1,
    }),
  );
  expect(request).not.toHaveBeenCalled();
});
