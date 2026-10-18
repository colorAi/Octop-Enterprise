import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import zh from "../../../locales/zh.json";
import en from "../../../locales/en.json";
import type { UpdateStatus } from "../../../api/modules/update";

const state = vi.hoisted(() => ({
  getUpdateStatus: vi.fn(),
  triggerUpgrade: vi.fn(),
  locale: "zh" as "zh" | "en",
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const update =
        state.locale === "zh"
          ? zh.advancedSettings.update
          : en.advancedSettings.update;
      return update[key.split(".").at(-1) as keyof typeof update] ?? key;
    },
  }),
}));
vi.mock("../../../api/modules/update", () => ({
  updateApi: {
    getUpdateStatus: state.getUpdateStatus,
    triggerUpgrade: state.triggerUpgrade,
  },
}));
vi.mock("../../../context/ServiceRestartContext", () => ({
  useServiceRestartContext: () => ({
    restartPhase: "idle",
    isRestarting: false,
  }),
}));
vi.mock("../../../components/Markdown/LazyMarkdown", () => ({
  default: () => null,
}));
vi.mock("./ClientConfigPublish", () => ({
  default: () => <div data-testid="config-publish" />,
}));

import UpdateConfig from "./UpdateConfig";

const manualStatus: UpdateStatus = {
  current_version: "1.0.0",
  latest_version: null,
  has_update: false,
  is_editable: false,
  service_mode: null,
  error: "manual distribution",
  error_code: "manual_update_required",
  source: null,
  last_check_time: null,
  release_notes: null,
};

function expectManualGuide(container: HTMLElement) {
  expect(screen.getByTestId("config-publish")).toBeInTheDocument();
  const copy =
    state.locale === "zh"
      ? zh.advancedSettings.update
      : en.advancedSettings.update;
  expect(
    screen.getByRole("region", { name: copy.manualTitle }),
  ).toBeInTheDocument();
  expect(screen.getByText(copy.manualConfig)).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: copy.upgradeButton }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: copy.checkButton }),
  ).not.toBeInTheDocument();
  expect(container.textContent).not.toMatch(
    /pip install|curl -fsSL|octop update|docker compose pull/,
  );
  expect(state.triggerUpgrade).not.toHaveBeenCalled();
}

describe("enterprise update instructions", () => {
  beforeEach(() => {
    state.locale = "zh";
    state.getUpdateStatus.mockReset();
    state.triggerUpgrade.mockReset();
  });

  it.each(["zh", "en"] as const)(
    "explains manual releases in %s without upstream commands",
    async (locale) => {
      state.locale = locale;
      state.getUpdateStatus.mockResolvedValue(manualStatus);
      const { container } = render(<UpdateConfig />);
      expectManualGuide(container);
      await act(async () => {
        await Promise.resolve();
      });
      expect(screen.getByText(/1\.0\.0/)).toBeInTheDocument();
      expectManualGuide(container);
    },
  );

  it("does not expose an upstream guide when the status request fails", async () => {
    state.getUpdateStatus.mockRejectedValue(new Error("offline"));
    const { container } = render(<UpdateConfig />);
    await act(async () => {
      await Promise.resolve();
    });
    expectManualGuide(container);
  });
});
