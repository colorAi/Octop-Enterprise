import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useDesktopChrome } from "./useDesktopChrome";

afterEach(() => {
  delete document.documentElement.dataset.octopDragReady;
  delete document.documentElement.dataset.octopDesktopChrome;
  vi.unstubAllGlobals();
});

it("removes page controls when native titlebar capability arrives after the bridge", () => {
  const invoke = vi.fn();
  const flags = { octopNativeTitlebar: false };
  vi.stubGlobal("_wails", { invoke, flags });
  const { result, unmount } = renderHook(() => useDesktopChrome());
  expect(result.current).toBe("windows");
  act(() => {
    flags.octopNativeTitlebar = true;
    window.dispatchEvent(new Event("octop:native-titlebar-ready"));
  });
  expect(result.current).toBeNull();
  expect(document.documentElement.dataset.octopDesktopChrome).toBeUndefined();
  const region = document.createElement("div");
  document.body.appendChild(region);
  region.dispatchEvent(
    new MouseEvent("mousedown", { button: 0, clientY: 10, bubbles: true }),
  );
  window.dispatchEvent(
    new MouseEvent("mousemove", { screenX: 30, screenY: 10 }),
  );
  region.dispatchEvent(
    new MouseEvent("dblclick", { button: 0, clientY: 10, bubbles: true }),
  );
  expect(invoke).not.toHaveBeenCalled();
  region.remove();
  unmount();
});
