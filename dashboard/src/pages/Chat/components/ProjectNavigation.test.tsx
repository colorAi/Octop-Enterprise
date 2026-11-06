import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "../../../api/desktopRuntime";
import type {
  DesktopProjectState,
  ProjectThread,
} from "../../../api/desktopProjects";
import type { OctopAgent } from "../../../context/AgentContext";
import { ProjectNavigation } from "./ProjectNavigation";
const p = vi.hoisted(() => ({
  state: null as DesktopProjectState | null,
  available: true,
  busy: false,
  error: "",
  revision: 0,
  select: vi.fn(),
  choose: vi.fn(),
  update: vi.fn(),
  relocate: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("../../../context/DesktopProjectsContext", () => ({
  useDesktopProjects: () => p,
}));
vi.mock("../hooks/chatStore", () => ({
  onSessionEvent: () => () => {},
  onStreamEvent: () => () => {},
}));
const agents = [
  { agent_id: "writer", name: "文案专家", kind: "expert" },
  { agent_id: "designer", name: "设计专家", kind: "expert" },
] as OctopAgent[];
const items: Record<string, ProjectThread[]> = {
  A: [
    {
      thread_id: "a1",
      project_id: "A",
      agent_id: "writer",
      title: "品牌文案",
      pinned: false,
    },
    {
      thread_id: "a2",
      project_id: "A",
      agent_id: "designer",
      title: "品牌设计图",
      pinned: false,
    },
  ],
  B: [
    {
      thread_id: "b1",
      project_id: "B",
      agent_id: "designer",
      title: "产品封面",
      pinned: false,
    },
  ],
};
const threads = vi.fn();
beforeEach(() => {
  p.state = {
    supported: true,
    current_project_id: "A",
    projects: {
      A: {
        id: "A",
        name: "品牌项目",
        path: "/brand",
        authorized: true,
        revision: 1,
      },
      B: {
        id: "B",
        name: "产品项目",
        path: "/product",
        authorized: true,
        revision: 1,
        last_expert: "designer",
      },
    },
    threads: { a1: "A", a2: "A", b1: "B" },
  };
  p.select.mockImplementation(
    async ({ project_id }: { project_id: string }) => ({
      ...p.state,
      current_project_id: project_id,
    }),
  );
  threads.mockImplementation(
    async ({ project_id }: { project_id: string }) => ({
      items: items[project_id],
      has_more: false,
    }),
  );
  window.__octopRuntime = {
    projects: { threads },
  } as unknown as DesktopRuntimeBridge;
});
afterEach(() => {
  delete window.__octopRuntime;
  vi.resetAllMocks();
});
function show(
  props: Partial<React.ComponentProps<typeof ProjectNavigation>> = {},
) {
  return render(
    <ProjectNavigation
      agents={agents}
      agentId="writer"
      threadId="a1"
      onNewChat={vi.fn()}
      onSelect={vi.fn()}
      {...props}
    >
      <span>专家快捷入口</span>
    </ProjectNavigation>,
  );
}
it("nests conversations under independent collapsible folders without navigating", async () => {
  const select = vi.fn();
  show({ onSelect: select });
  const a = screen.getByRole("region", { name: "品牌项目项目" });
  const b = screen.getByRole("region", { name: "产品项目项目" });
  expect(
    screen
      .getByRole("button", { name: "添加项目文件夹" })
      .compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(await within(a).findByText("品牌设计图")).toBeInTheDocument();
  expect(within(b).queryByText("产品封面")).not.toBeInTheDocument();
  expect(threads).not.toHaveBeenCalledWith({ project_id: "B" });
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  fireEvent.click(within(b).getByRole("button", { name: "展开项目 产品项目" }));
  expect(await within(b).findByText("产品封面")).toBeInTheDocument();
  expect(within(a).getByText("品牌设计图")).toBeInTheDocument();
  fireEvent.click(within(a).getByRole("button", { name: "折叠项目 品牌项目" }));
  expect(within(a).queryByText("品牌设计图")).not.toBeInTheDocument();
  expect(within(b).getByText("产品封面")).toBeInTheDocument();
  expect(p.select).not.toHaveBeenCalled();
  expect(select).not.toHaveBeenCalled();
  expect(screen.queryByText("专家快捷入口")).not.toBeInTheDocument();
});
it("selects a conversation with its original project and expert", async () => {
  const select = vi.fn();
  show({ onSelect: select });
  fireEvent.click(screen.getByRole("button", { name: "展开项目 产品项目" }));
  fireEvent.click(await screen.findByRole("button", { name: /产品封面/ }));
  await waitFor(() => expect(select).toHaveBeenCalledWith("b1", "designer"));
  expect(p.select).toHaveBeenCalledWith({
    project_id: "B",
    thread_id: "b1",
    agent_id: "designer",
  });
});
it("starts with the expert selected on a different folder and passes its explicit project", async () => {
  const start = vi.fn();
  show({ onNewChat: start });
  fireEvent.click(screen.getByRole("button", { name: "在 产品项目 新建对话" }));
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "设计专家 · 最近使用" }),
  );
  await waitFor(() => expect(start).toHaveBeenCalledWith("designer", "B"));
  expect(p.select).toHaveBeenCalledWith({
    project_id: "B",
    agent_id: "designer",
  });
  expect(
    screen.getByRole("button", { name: "折叠项目 产品项目" }),
  ).toHaveAttribute("aria-expanded", "true");
});
it("paginates each folder without mixing conversations", async () => {
  threads.mockImplementation(
    async ({
      project_id,
      offset,
    }: {
      project_id: string;
      offset?: number;
    }) => ({
      items: offset
        ? [{ ...items.B[0], thread_id: "b2", title: "包装方案" }]
        : items[project_id],
      has_more: project_id === "B" && !offset,
    }),
  );
  show();
  fireEvent.click(screen.getByRole("button", { name: "展开项目 产品项目" }));
  const b = screen.getByRole("region", { name: "产品项目项目" });
  fireEvent.click(await within(b).findByRole("button", { name: "更多对话" }));
  expect(await within(b).findByText("包装方案")).toBeInTheDocument();
  expect(within(b).getByText("产品封面")).toBeInTheDocument();
  expect(
    within(screen.getByRole("region", { name: "品牌项目项目" })).queryByText(
      "包装方案",
    ),
  ).not.toBeInTheDocument();
  expect(threads).toHaveBeenCalledWith({ project_id: "B", offset: 1 });
});
it("ignores an old folder request after collapse and reopen", async () => {
  let resolveOld!: (value: {
    items: ProjectThread[];
    has_more: boolean;
  }) => void;
  threads.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveOld = resolve;
      }),
  );
  show();
  fireEvent.click(screen.getByRole("button", { name: "折叠项目 品牌项目" }));
  fireEvent.click(screen.getByRole("button", { name: "展开项目 品牌项目" }));
  await screen.findByText("品牌文案");
  await act(async () =>
    resolveOld({
      items: [{ ...items.A[0], title: "过期结果" }],
      has_more: false,
    }),
  );
  expect(screen.queryByText("过期结果")).not.toBeInTheDocument();
  expect(screen.getByText("品牌文案")).toBeInTheDocument();
});
it("targets the folder being managed instead of the selected project", async () => {
  p.update.mockResolvedValue(p.state);
  show();
  fireEvent.click(screen.getByRole("button", { name: "管理项目 产品项目" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "重命名项目" }));
  fireEvent.change(await screen.findByRole("textbox", { name: "项目名称" }), {
    target: { value: "产品资料" },
  });
  fireEvent.click(screen.getByRole("button", { name: /OK|确 定/ }));
  await waitFor(() =>
    expect(p.update).toHaveBeenCalledWith({
      project_id: "B",
      name: "产品资料",
    }),
  );
});
it("does not start a new conversation after a cancelled folder selection", async () => {
  p.choose.mockResolvedValue({ ...p.state, cancelled: true });
  const start = vi.fn();
  show({ onNewChat: start });
  await act(async () =>
    fireEvent.click(screen.getByRole("button", { name: /添加项目文件夹/ })),
  );
  expect(start).not.toHaveBeenCalled();
});
