import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "../api/desktopRuntime";
import { getDesktopProjectSnapshot } from "../api/desktopProjects";
import {
  DesktopProjectsProvider,
  useDesktopProjects,
} from "./DesktopProjectsContext";
const workspace = vi.hoisted(() => ({
  state: { owner_user_id: 1, state: "online" },
}));
vi.mock("./DesktopWorkspaceContext", () => ({
  useDesktopWorkspace: () => workspace,
}));
afterEach(() => {
  delete window.__octopRuntime;
  workspace.state.owner_user_id = 1;
});
it("ignores a late catalog reply after switching employees", async () => {
  let finish: ((value: unknown) => void) | undefined;
  const status = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValue({
      supported: true,
      current_project_id: "B",
      projects: {},
      threads: {},
    });
  window.__octopRuntime = {
    projects: { status },
  } as unknown as DesktopRuntimeBridge;
  const { result, rerender, unmount } = renderHook(() => useDesktopProjects(), {
    wrapper: DesktopProjectsProvider,
  });
  await waitFor(() => expect(status).toHaveBeenCalledTimes(1));
  workspace.state.owner_user_id = 2;
  rerender();
  await waitFor(() =>
    expect(result.current.state?.current_project_id).toBe("B"),
  );
  await act(async () => {
    finish!({
      supported: true,
      current_project_id: "A",
      projects: {},
      threads: {},
    });
  });
  expect(result.current.state?.current_project_id).toBe("B");
  expect(getDesktopProjectSnapshot()?.current_project_id).toBe("B");
  unmount();
  expect(getDesktopProjectSnapshot()).toBeNull();
});
it.each(["choose", "relocate"] as const)(
  "keeps the existing project when %s is cancelled",
  async (action) => {
    const saved = {
      supported: true,
      current_project_id: "A",
      projects: {},
      threads: {},
    };
    window.__octopRuntime = {
      projects: {
        status: vi.fn().mockResolvedValue(saved),
        choose: vi.fn().mockResolvedValue({ ...saved, cancelled: true }),
        relocate: vi.fn().mockResolvedValue({ ...saved, cancelled: true }),
      },
    } as unknown as DesktopRuntimeBridge;
    const { result } = renderHook(() => useDesktopProjects(), {
      wrapper: DesktopProjectsProvider,
    });
    await waitFor(() =>
      expect(result.current.state?.current_project_id).toBe("A"),
    );
    await act(async () => {
      const next =
        action === "choose"
          ? await result.current.choose()
          : await result.current.relocate("A");
      expect(next?.cancelled).toBe(true);
    });
    expect(result.current.state?.current_project_id).toBe("A");
    expect(result.current.error).toBe("");
    expect(result.current.busy).toBe(false);
    expect(getDesktopProjectSnapshot()?.current_project_id).toBe("A");
  },
);
