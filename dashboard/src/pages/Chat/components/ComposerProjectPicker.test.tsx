import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { DesktopProjectState } from "../../../api/desktopProjects";
import ComposerProjectPicker from "./ComposerProjectPicker";

const p = vi.hoisted(() => ({
  available: true,
  busy: false,
  state: null as DesktopProjectState | null,
  select: vi.fn(),
  choose: vi.fn(),
  relocate: vi.fn(),
}));
vi.mock("../../../context/DesktopProjectsContext", () => ({
  useDesktopProjects: () => p,
}));
beforeEach(() => {
  vi.resetAllMocks();
  p.available = true;
  p.busy = false;
  p.state = {
    supported: true,
    current_project_id: "B",
    projects: {
      A: {
        id: "A",
        name: "品牌项目",
        path: "/Users/employee/品牌资料",
        authorized: true,
        revision: 1,
      },
      B: {
        id: "B",
        name: "官网项目",
        path: "D:\\work\\产品官网\\",
        authorized: true,
        revision: 1,
      },
    },
    threads: { old: "A" },
  };
  p.select.mockImplementation(async ({ project_id }) => ({
    ...p.state,
    current_project_id: project_id,
  }));
});

it("shows the bound folder's basename and keeps its full path only in the tooltip", () => {
  render(<ComposerProjectPicker threadId="old" onProjectChange={vi.fn()} />);
  expect(
    screen.getByRole("button", { name: "项目文件夹：品牌资料" }),
  ).toHaveAttribute("title", "/Users/employee/品牌资料");
  expect(
    screen.queryByText("/Users/employee/品牌资料"),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("产品官网")).not.toBeInTheDocument();
  expect(p.select).toHaveBeenCalledWith({ project_id: "A", thread_id: "old" });
});
it("changes a draft to a saved project with the current expert", async () => {
  const change = vi.fn();
  render(
    <ComposerProjectPicker
      threadId={null}
      agentId="writer"
      onProjectChange={change}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "项目文件夹：产品官网" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "品牌资料" }));
  await waitFor(() => expect(change).toHaveBeenCalledOnce());
  expect(p.select).toHaveBeenCalledWith({
    project_id: "A",
    agent_id: "writer",
  });
});
it("starts a new draft in another project without rebinding the old conversation", async () => {
  const change = vi.fn();
  render(
    <ComposerProjectPicker
      threadId="old"
      agentId="writer"
      onProjectChange={change}
    />,
  );
  p.select.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "项目文件夹：品牌资料" }));
  expect(
    await screen.findByText("选择其他文件夹将在该项目新建对话"),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("menuitem", { name: "产品官网" }));
  await waitFor(() => expect(change).toHaveBeenCalledOnce());
  expect(p.select).toHaveBeenCalledExactlyOnceWith({
    project_id: "B",
    agent_id: "writer",
  });
  expect(p.state?.threads.old).toBe("A");
});
it("allows choosing the first folder when no project is selected", async () => {
  p.state = {
    supported: true,
    current_project_id: "",
    projects: {},
    threads: {},
  };
  p.choose.mockResolvedValue({ ...p.state, current_project_id: "new" });
  const change = vi.fn();
  render(<ComposerProjectPicker threadId={null} onProjectChange={change} />);
  fireEvent.click(
    screen.getByRole("button", { name: "项目文件夹：选择文件夹" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "选择其他文件夹…" }),
  );
  await waitFor(() => expect(change).toHaveBeenCalledOnce());
});
it.each(["cancelled", "failed", "same"])(
  "keeps the conversation after a %s folder choice",
  async (result) => {
    p.choose.mockResolvedValue(
      result === "failed"
        ? null
        : {
            ...p.state,
            cancelled: result === "cancelled",
            current_project_id: "A",
          },
    );
    const change = vi.fn();
    render(<ComposerProjectPicker threadId="old" onProjectChange={change} />);
    fireEvent.click(
      screen.getByRole("button", { name: "项目文件夹：品牌资料" }),
    );
    const choose = await screen.findByRole("menuitem", {
      name: "选择其他文件夹…",
    });
    await act(async () => fireEvent.click(choose));
    expect(p.choose).toHaveBeenCalledOnce();
    expect(change).not.toHaveBeenCalled();
  },
);
it("offers reauthorization for the original folder without starting another conversation", async () => {
  p.state!.projects.A.authorized = false;
  const change = vi.fn();
  render(<ComposerProjectPicker threadId="old" onProjectChange={change} />);
  fireEvent.click(screen.getByRole("button", { name: "项目文件夹：品牌资料" }));
  expect(
    await screen.findByRole("menuitem", { name: "品牌资料 · 需授权" }),
  ).toHaveAttribute("aria-disabled", "true");
  fireEvent.click(
    screen.getByRole("menuitem", { name: "重新定位原文件夹并授权" }),
  );
  expect(p.relocate).toHaveBeenCalledWith("A");
  expect(change).not.toHaveBeenCalled();
});
it("does not show a project picker outside supported desktop mode", () => {
  p.available = false;
  const { rerender } = render(
    <ComposerProjectPicker threadId={null} onProjectChange={vi.fn()} />,
  );
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
  p.available = true;
  p.state!.supported = false;
  rerender(<ComposerProjectPicker threadId={null} onProjectChange={vi.fn()} />);
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
