import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "../../../api/desktopRuntime";
import type { OctopAgent } from "../../../context/AgentContext";
import { ProjectNavigation } from "./ProjectNavigation";
const p = vi.hoisted(() => ({
  state: {
    supported: true,
    current_project_id: "A",
    projects: {
      A: { id: "A", name: "设计项目", path: "/design", authorized: true },
    },
    threads: {},
  },
  available: true,
  busy: false,
  error: "",
  revision: 0,
  select: vi.fn(),
  choose: vi.fn(),
  update: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("../../../context/DesktopProjectsContext", () => ({
  useDesktopProjects: () => p,
}));
vi.mock("../hooks/chatStore", () => ({
  onSessionEvent: () => () => {},
  onStreamEvent: () => () => {},
}));
afterEach(() => {
  delete window.__octopRuntime;
  vi.clearAllMocks();
});
it("lists conversations across experts and retains the expert shortcut", async () => {
  const items = [
    {
      thread_id: "t1",
      project_id: "A",
      agent_id: "writer",
      title: "文案",
      pinned: false,
    },
    {
      thread_id: "t2",
      project_id: "A",
      agent_id: "designer",
      title: "设计图",
      pinned: false,
    },
  ];
  window.__octopRuntime = {
    projects: {
      threads: vi.fn().mockResolvedValue({ items, has_more: false }),
    },
  } as unknown as DesktopRuntimeBridge;
  p.select.mockResolvedValue(p.state);
  const select = vi.fn();
  render(
    <ProjectNavigation
      agents={
        [
          { agent_id: "writer", name: "文案专家", kind: "expert" },
          { agent_id: "designer", name: "设计专家", kind: "expert" },
        ] as OctopAgent[]
      }
      agentId="writer"
      threadId="t1"
      onNewChat={vi.fn()}
      onSelect={select}
    >
      <span>专家快捷入口</span>
    </ProjectNavigation>,
  );
  await screen.findByText("设计图");
  fireEvent.click(screen.getByRole("button", { name: /设计图/ }));
  await waitFor(() => expect(select).toHaveBeenCalledWith("t2", "designer"));
  expect(p.select).toHaveBeenCalledWith({
    project_id: "A",
    thread_id: "t2",
    agent_id: "designer",
  });
  expect(screen.getByText("专家快捷入口")).toBeInTheDocument();
});
it("does not start a new conversation after a cancelled folder selection", async () => {
  window.__octopRuntime = {
    projects: {
      threads: vi.fn().mockResolvedValue({ items: [], has_more: false }),
    },
  } as unknown as DesktopRuntimeBridge;
  p.choose.mockResolvedValue({ ...p.state, cancelled: true });
  const start = vi.fn();
  render(
    <ProjectNavigation
      agents={
        [
          { agent_id: "writer", name: "文案专家", kind: "expert" },
        ] as OctopAgent[]
      }
      agentId="writer"
      threadId="t1"
      onNewChat={start}
      onSelect={vi.fn()}
    >
      <span>history</span>
    </ProjectNavigation>,
  );
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /添加项目文件夹/ }));
  });
  expect(start).not.toHaveBeenCalled();
});
