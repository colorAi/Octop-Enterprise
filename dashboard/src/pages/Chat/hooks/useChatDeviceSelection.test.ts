import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useChatDeviceSelection } from "./useChatDeviceSelection";
import type { DesktopWorkspaceState } from "@/api/desktopWorkspace";

const context = vi.hoisted(() => ({
  available: true,
  error: "",
  state: null as DesktopWorkspaceState | null,
}));
vi.mock("@/context/DesktopWorkspaceContext", () => ({
  useDesktopWorkspace: () => context,
}));
beforeEach(() => {
  delete window.__octopRuntime;
  context.available = true;
  context.error = "";
  context.state = {
    state: "online",
    device_id: "this-computer",
    owner_user_id: 7,
    path: "/work",
    grant: {
      id: "work",
      name: "Work",
      version: 1,
      writable: true,
      allow_company: true,
    },
  };
});
describe("desktop task location", () => {
  it("uses the current computer automatically and follows a changed working folder", () => {
    const { result, rerender } = renderHook(() =>
      useChatDeviceSelection("expert", false),
    );
    expect(result.current.device).toEqual({
      mode: "auto",
      device_id: "this-computer",
      grant_id: "work",
      grant_version: 1,
    });
    context.state!.grant!.id = "other-work";
    rerender();
    expect(result.current.device?.grant_id).toBe("other-work");
  });
  it("retains local intent on disconnect, removal and directory errors", () => {
    const { result, rerender } = renderHook(() =>
      useChatDeviceSelection("expert", false),
    );
    context.state!.state = "disconnected";
    rerender();
    expect(result.current.device).toMatchObject({ mode: "auto", grant_id: "" });
    context.state = {
      ...context.state!,
      state: "online",
      grant: null,
      path: "",
    };
    rerender();
    expect(result.current.device).toMatchObject({ mode: "auto", grant_id: "" });
  });
  it("leaves company runtime routing to the native task entry", () => {
    window.__octopRuntime = { version: 1 } as Window["__octopRuntime"];
    const { result, rerender } = renderHook(() =>
      useChatDeviceSelection("expert", false),
    );
    expect(result.current.device).toBeNull();
    context.state!.grant!.id = "new-work";
    rerender();
    expect(result.current.device).toBeNull();
  });
  it("starts each expert with the current work folder and excludes unsupported teams", () => {
    const { result, rerender } = renderHook(
      ({ scope, unsupported }) => useChatDeviceSelection(scope, unsupported),
      { initialProps: { scope: "one", unsupported: false } },
    );
    rerender({ scope: "two", unsupported: false });
    expect(result.current.device?.mode).toBe("auto");
    rerender({ scope: "team", unsupported: true });
    expect(result.current.device).toBeNull();
  });
  it("does not assume a browser is this computer", () => {
    context.available = false;
    const { result } = renderHook(() =>
      useChatDeviceSelection("expert", false),
    );
    expect(result.current.device).toBeNull();
  });
});
