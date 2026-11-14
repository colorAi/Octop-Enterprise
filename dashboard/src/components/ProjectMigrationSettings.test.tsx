import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "../api/desktopRuntime";
import { PROJECTS_CHANGED } from "../api/desktopProjects";
import ProjectMigrationSettings from "./ProjectMigrationSettings";
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import zh from "../locales/zh.json";

const bridge = vi.hoisted(() => ({
  export: vi.fn(),
  import: vi.fn(),
  sync: vi.fn(),
  accountProjects: vi.fn(),
  attach: vi.fn(),
}));
vi.unmock("react-i18next");
vi.mock("../context/DesktopProjectsContext", () => ({
  useDesktopProjects: () => ({
    state: {
      current_project_id: "a",
      projects: {
        a: {
          id: "a",
          name: "Website",
          path: "/work/website",
          authorized: true,
        },
      },
    },
  }),
}));

beforeEach(async () => {
  await i18n.use(initReactI18next).init({
    lng: "zh",
    resources: { zh: { translation: zh } },
    interpolation: { escapeValue: false },
  });
  bridge.export.mockReset().mockResolvedValue({ path: "/backup/project.zip" });
  bridge.import.mockReset().mockResolvedValue({
    path: "/new/location",
    project_ids: ["a"],
    synced_count: 2,
  });
  bridge.sync.mockReset().mockResolvedValue({ synced_count: 3 });
  bridge.accountProjects
    .mockReset()
    .mockResolvedValue([{ project_id: "b", name: "Account project" }]);
  bridge.attach.mockReset().mockResolvedValue({
    path: "/copied/folder",
    project_ids: ["b"],
    synced_count: 4,
  });
  window.__octopRuntime = {
    projects: bridge,
  } as unknown as DesktopRuntimeBridge;
});
afterEach(() => {
  delete window.__octopRuntime;
  localStorage.removeItem("auth_token");
});

it("exports selected project IDs and refreshes the catalog after importing", async () => {
  const refreshed = vi.fn();
  window.addEventListener(PROJECTS_CHANGED, refreshed);
  render(<ProjectMigrationSettings online />);
  const exporting = screen.getByRole("button", { name: "导出迁移包" });
  expect(exporting).toBeDisabled();
  fireEvent.mouseDown(screen.getByRole("combobox", { name: "选择项目" }));
  fireEvent.click(await screen.findByText("Website · /work/website"));
  fireEvent.click(exporting);
  await waitFor(() =>
    expect(bridge.export).toHaveBeenCalledWith({ project_ids: ["a"] }),
  );
  expect(await screen.findByText(/迁移包已保存/)).toHaveTextContent(
    "/backup/project.zip",
  );
  fireEvent.click(screen.getByRole("button", { name: "导入迁移包" }));
  await waitFor(() => expect(refreshed).toHaveBeenCalledTimes(1));
  expect(screen.getByText(/项目已导入/)).toHaveTextContent("/new/location");
  expect(screen.getByText(/项目已导入/)).toHaveTextContent("已同步 2 个对话");
  expect(bridge.sync).not.toHaveBeenCalled();
  window.removeEventListener(PROJECTS_CHANGED, refreshed);
});

it("keeps a local import pending while disconnected and lets the user sync later", async () => {
  bridge.import.mockResolvedValue({
    path: "/new/location",
    project_ids: ["a"],
    sync_pending: true,
  });
  const { rerender } = render(
    <ProjectMigrationSettings online={false} canImport />,
  );
  fireEvent.click(screen.getByRole("button", { name: "导入迁移包" }));
  expect(await screen.findByText(/联网后可点击/)).toHaveTextContent(
    "/new/location",
  );
  expect(screen.getByRole("button", { name: "同步对话" })).toBeDisabled();
  rerender(<ProjectMigrationSettings online />);
  fireEvent.click(screen.getByRole("button", { name: "同步对话" }));
  expect(await screen.findByText("已同步 3 个项目对话。")).toBeInTheDocument();
  expect(bridge.sync).toHaveBeenCalledWith({ project_ids: ["a"] });
});

it("lets a user link files they already copied to an account project", async () => {
  render(<ProjectMigrationSettings online />);
  fireEvent.click(screen.getByRole("button", { name: "关联本机目录" }));
  fireEvent.mouseDown(
    await screen.findByRole("combobox", { name: "选择账号下的项目" }),
  );
  fireEvent.click(await screen.findByText("Account project"));
  fireEvent.click(screen.getByRole("button", { name: "选择文件夹" }));
  expect(await screen.findByText(/已关联目录/)).toHaveTextContent(
    "/copied/folder",
  );
  expect(bridge.attach).toHaveBeenCalledWith({ project_id: "b" });
});

it("discards an import reply after the login changes", async () => {
  localStorage.setItem("auth_token", "account-a");
  let finish!: (result: { path: string }) => void;
  bridge.import.mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  render(<ProjectMigrationSettings online />);
  fireEvent.click(screen.getByRole("button", { name: "导入迁移包" }));
  localStorage.setItem("auth_token", "account-b");
  await act(async () => finish({ path: "private-A-project" }));
  expect(screen.queryByText(/private-A-project/)).toBeNull();
});
