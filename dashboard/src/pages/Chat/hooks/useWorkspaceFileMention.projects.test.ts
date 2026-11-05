import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useWorkspaceFileMention } from "./useWorkspaceFileMention";
import type { DesktopRuntimeBridge } from "../../../api/desktopRuntime";
import { request } from "../../../api/request";
vi.mock("../../../api/request", () => ({ request: vi.fn() }));
vi.mock("../../../context/DesktopProjectsContext", () => ({
  useDesktopProjects: () => ({
    available: true,
    state: {
      supported: true,
      current_project_id: "B",
      threads: { "chat-a": "A" },
    },
  }),
}));
afterEach(() => {
  vi.useRealTimers();
  delete window.__octopRuntime;
  vi.clearAllMocks();
});
it("searches the open chat's local folder instead of the selected project or server workspace", async () => {
  vi.useFakeTimers();
  const files = vi.fn().mockResolvedValue({
    items: [{ path: "docs/report.md", is_dir: false }],
    truncated: false,
  });
  window.__octopRuntime = {
    projects: { files },
  } as unknown as DesktopRuntimeBridge;
  const { result } = renderHook(() =>
    useWorkspaceFileMention("writer", true, "report", "chat-a"),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(files).toHaveBeenCalledWith({
    project_id: "A",
    thread_id: "chat-a",
    query: "report",
  });
  expect(result.current.files[0].path).toBe("docs/report.md");
  expect(request).not.toHaveBeenCalled();
});
it("does not fall back to company files when local project lookup fails", async () => {
  vi.useFakeTimers();
  window.__octopRuntime = {
    projects: { files: vi.fn().mockRejectedValue(new Error("missing")) },
  } as unknown as DesktopRuntimeBridge;
  const { result } = renderHook(() =>
    useWorkspaceFileMention("writer", true, "report", "chat-missing"),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
  expect(result.current.error).toBe("failed");
  expect(request).not.toHaveBeenCalled();
});
