import { afterEach, expect, it, vi } from "vitest";
import {
  OPEN_WORKSPACE_SETTINGS,
  openWorkspaceSettings,
} from "./desktopWorkspace";
import { resolveSelectedKey } from "../routes";

afterEach(() => vi.restoreAllMocks());

it("opens the dedicated workspace settings route from local-task prompts", () => {
  const listener = vi.fn();
  window.addEventListener(OPEN_WORKSPACE_SETTINGS, listener);
  try {
    openWorkspaceSettings();
    expect(listener).toHaveBeenCalledOnce();
    expect(resolveSelectedKey("/settings/workspace")).toBe("local-workspace");
  } finally {
    window.removeEventListener(OPEN_WORKSPACE_SETTINGS, listener);
  }
});
