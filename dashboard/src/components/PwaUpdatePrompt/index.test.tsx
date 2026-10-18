import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";

const state = vi.hoisted(() => ({
  user: { role: "user", permissions: [] as string[] },
  getUpdateStatus: vi.fn(),
  restartService: vi.fn(),
  applyUpdate: vi.fn(),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => state.user,
}));
vi.mock("../../api/modules/update", () => ({ updateApi: state }));
vi.mock("../../pwa", () => ({ applyUpdate: state.applyUpdate }));

import PwaUpdatePrompt from ".";

describe("managed update prompts", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_DESKTOP_BUNDLE", "");
    state.user = { role: "user", permissions: [] };
    state.getUpdateStatus
      .mockReset()
      .mockResolvedValue({ service_mode: "systemd" });
    state.restartService.mockReset();
    state.applyUpdate.mockReset();
  });
  afterEach(() => {
    cleanup();
    delete window.__octopRuntime;
    vi.unstubAllEnvs();
  });

  it.each(["employee", "desktop admin", "bundled admin"])(
    "hides updates for %s",
    async (mode) => {
      if (mode !== "employee") state.user.role = "admin";
      if (mode === "desktop admin")
        Object.defineProperty(window, "__octopRuntime", {
          value: { version: 1 },
          configurable: true,
        });
      if (mode === "bundled admin") vi.stubEnv("VITE_DESKTOP_BUNDLE", "1");
      render(<PwaUpdatePrompt />);
      await act(async () => {
        window.dispatchEvent(new CustomEvent("pwa:update-ready"));
      });
      expect(screen.queryByRole("alert")).toBeNull();
      expect(state.getUpdateStatus).not.toHaveBeenCalled();
      expect(state.restartService).not.toHaveBeenCalled();
      expect(state.applyUpdate).not.toHaveBeenCalled();
    },
  );

  it("retains browser updates for an authorized administrator", async () => {
    state.user.role = "admin";
    render(<PwaUpdatePrompt />);
    await act(async () => {
      window.dispatchEvent(new CustomEvent("pwa:update-ready"));
    });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(state.getUpdateStatus).toHaveBeenCalledOnce();
  });
});
