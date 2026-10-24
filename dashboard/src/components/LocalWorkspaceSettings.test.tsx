import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import LocalWorkspaceSettings from "./LocalWorkspaceSettings";

const mock = vi.hoisted(() => ({
  t: (key: string) => key,
  status: vi.fn(),
  reconcile: vi.fn(),
  error: vi.fn(),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: mock.t }) }));
vi.mock("@/utils/antdMessage", () => ({ message: { error: mock.error } }));
vi.mock("../api/desktopRuntime", () => ({ requireDesktopRuntime: () => mock }));
vi.mock("../context/DesktopWorkspaceContext", () => ({
  useDesktopWorkspace: () => ({
    available: true,
    state: { state: "online", path: "/workspace", grant: { id: "grant" } },
  }),
}));

beforeEach(() => {
  mock.status.mockReset().mockResolvedValue({ tools: {}, active_tasks: [] });
  mock.reconcile.mockReset();
  mock.error.mockReset();
});

it("shows progress and completion when manually checking an empty tool result", async () => {
  render(<LocalWorkspaceSettings />);
  expect(await screen.findByText("devices.runtimeReady")).toBeVisible();
  expect(mock.status).toHaveBeenLastCalledWith();
  let finish!: (result: object) => void;
  mock.status.mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const button = screen.getByRole("button", { name: "devices.runtimeCheck" });
  fireEvent.click(button);
  await waitFor(() => expect(button).toBeDisabled());
  expect(button).toHaveClass("ant-btn-loading");
  await act(async () => finish({ tools: {}, active_tasks: [] }));
  expect(await screen.findByText("devices.runtimeReady")).toBeVisible();
  expect(button).toBeEnabled();
  expect(mock.status).toHaveBeenCalledTimes(2);
  expect(mock.status).toHaveBeenLastCalledWith({ verify_tools: true });
});

it("shows a tool-check failure and can check successfully again", async () => {
  mock.status.mockRejectedValueOnce(new Error("tool startup failed"));
  render(<LocalWorkspaceSettings />);
  expect(await screen.findByText("tool startup failed")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "devices.runtimeCheck" }));
  expect(await screen.findByText("devices.runtimeReady")).toBeVisible();
  expect(screen.queryByText("tool startup failed")).toBeNull();
});

it("closes the selected missing task and refreshes the warning without replaying it", async () => {
  mock.status
    .mockResolvedValueOnce({
      active_tasks: [
        {
          task_id: "task",
          thread_id: "thread",
          status: "unknown",
          recovery_state: "missing",
        },
      ],
    })
    .mockResolvedValue({ active_tasks: [] });
  let finish: (() => void) | undefined;
  mock.reconcile.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  render(<LocalWorkspaceSettings />);
  expect(await screen.findByText("devices.runtimeTaskMissing")).toBeVisible();
  const action = screen.getByRole("button", {
    name: "devices.runtimeReconcileAction",
  });
  fireEvent.click(action);
  expect(mock.reconcile).toHaveBeenCalledWith({
    thread_id: "thread",
    task_id: "task",
    resolution: "failed",
  });
  expect(action).toHaveClass("ant-btn-loading");
  finish!();
  await waitFor(() =>
    expect(screen.queryByText("devices.runtimeTaskMissing")).toBeNull(),
  );
  expect(mock.error).not.toHaveBeenCalled();
});
