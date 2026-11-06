import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import HistoryContinuation from "./HistoryContinuation";

const mocks = vi.hoisted(() => ({
  agents: [] as Array<Record<string, unknown>>,
  refresh: vi.fn(),
  resume: vi.fn(),
  instantiate: vi.fn(),
  projects: {
    available: true,
    busy: false,
    error: "",
    choose: vi.fn(),
    state: {
      current_project_id: "current",
      threads: {} as Record<string, string>,
      projects: {
        original: { id: "original", name: "Original", authorized: true },
        current: { id: "current", name: "Current", authorized: true },
      },
    },
  },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../../context/AgentContext", () => ({ useAgent: () => mocks }));
vi.mock("../../../context/DesktopProjectsContext", () => ({
  useDesktopProjects: () => mocks.projects,
}));
vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { continue: mocks.resume },
}));
vi.mock("../../../api/modules/octopAgents", () => ({
  octopAgentsApi: { privateInstance: mocks.instantiate },
}));

beforeEach(() => {
  vi.resetAllMocks();
  mocks.agents = [
    {
      agent_id: "old",
      name: "Old",
      history_only: true,
      is_owner: true,
      state: "running",
    },
    {
      agent_id: "current",
      name: "Current expert",
      is_owner: true,
      state: "running",
    },
  ];
  mocks.projects.available = true;
  mocks.projects.busy = false;
  mocks.projects.error = "";
  mocks.projects.state.current_project_id = "current";
  mocks.projects.state.threads = { oldchat: "original" };
  mocks.resume.mockResolvedValue({
    agent_id: "current",
    thread_id: "continued",
  });
  mocks.refresh.mockResolvedValue(undefined);
});

it("defaults to the same expert's current instance and the original project", async () => {
  const continued = vi.fn();
  render(
    <HistoryContinuation
      agentId="old"
      threadId="oldchat"
      recommendedAgentId="current"
      onContinued={continued}
    />,
  );
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.queryByText("historyContinue.title")).not.toBeInTheDocument();
  expect(screen.queryByText("historyContinue.hint")).not.toBeInTheDocument();
  expect(screen.getAllByRole("button")).toHaveLength(1);
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  await waitFor(() =>
    expect(continued).toHaveBeenCalledWith("current", "continued"),
  );
  expect(mocks.resume).toHaveBeenCalledExactlyOnceWith(
    "old",
    "oldchat",
    "current",
    "original",
  );
  expect(mocks.projects.choose).not.toHaveBeenCalled();
});

it("continues server history with the existing expert and the selected local project", async () => {
  render(
    <HistoryContinuation
      agentId="current"
      threadId="server-chat"
      onContinued={vi.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  await waitFor(() =>
    expect(mocks.resume).toHaveBeenCalledWith(
      "current",
      "server-chat",
      "current",
      "current",
    ),
  );
});

it("shows available replacements only after clicking the inline action", async () => {
  render(
    <HistoryContinuation
      agentId="old"
      threadId="oldchat"
      onContinued={vi.fn()}
    />,
  );
  const button = screen.getByRole("button", { name: "historyContinue.action" });
  expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  fireEvent.click(button);
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Current expert" }),
  );
  await waitFor(() =>
    expect(mocks.resume).toHaveBeenCalledWith(
      "old",
      "oldchat",
      "current",
      "original",
    ),
  );
});

it("materializes the currently assigned private instance before continuing", async () => {
  mocks.agents = [
    {
      agent_id: "source",
      name: "Assigned",
      requires_private_instance: true,
      managed: true,
    },
  ];
  mocks.instantiate.mockResolvedValue({ agent_id: "private-current" });
  render(
    <HistoryContinuation
      agentId="old"
      threadId="oldchat"
      recommendedAgentId="source"
      onContinued={vi.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  await waitFor(() =>
    expect(mocks.resume).toHaveBeenCalledWith(
      "old",
      "oldchat",
      "private-current",
      "original",
    ),
  );
  expect(mocks.instantiate).toHaveBeenCalledExactlyOnceWith("source");
});

it("keeps the source conversation and original folder after continuation failure", async () => {
  const continued = vi.fn();
  mocks.resume.mockRejectedValue(new Error("resource_not_assigned"));
  render(
    <HistoryContinuation
      agentId="old"
      threadId="oldchat"
      recommendedAgentId="current"
      onContinued={continued}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "historyContinue.failed",
  );
  expect(mocks.resume).toHaveBeenCalledWith(
    "old",
    "oldchat",
    "current",
    "original",
  );
  expect(continued).not.toHaveBeenCalled();
});

it("asks for a project only when needed and leaves the history open on cancellation", async () => {
  const continued = vi.fn();
  mocks.projects.state.threads = {};
  mocks.projects.state.current_project_id = "";
  mocks.projects.choose.mockResolvedValue({ cancelled: true });
  render(
    <HistoryContinuation
      agentId="old"
      threadId="oldchat"
      recommendedAgentId="current"
      onContinued={continued}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  await waitFor(() => expect(mocks.projects.choose).toHaveBeenCalledOnce());
  expect(mocks.resume).not.toHaveBeenCalled();
  expect(continued).not.toHaveBeenCalled();

  mocks.projects.choose.mockResolvedValue({
    ...mocks.projects.state,
    current_project_id: "current",
  });
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  await waitFor(() =>
    expect(continued).toHaveBeenCalledWith("current", "continued"),
  );
  expect(mocks.resume).toHaveBeenCalledExactlyOnceWith(
    "old",
    "oldchat",
    "current",
    "current",
  );
});

it("continues web history without asking for a local project", async () => {
  mocks.projects.available = false;
  mocks.projects.state.threads = {};
  mocks.projects.state.current_project_id = "";
  render(
    <HistoryContinuation
      agentId="old"
      threadId="oldchat"
      recommendedAgentId="current"
      onContinued={vi.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  await waitFor(() =>
    expect(mocks.resume).toHaveBeenCalledExactlyOnceWith(
      "old",
      "oldchat",
      "current",
      undefined,
    ),
  );
  expect(mocks.projects.choose).not.toHaveBeenCalled();
});

it("does not navigate back when a continuation completes after leaving the conversation", async () => {
  const continued = vi.fn();
  let finish!: (value: unknown) => void;
  mocks.resume.mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const view = render(
    <HistoryContinuation
      agentId="old"
      threadId="oldchat"
      recommendedAgentId="current"
      onContinued={continued}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "historyContinue.action" }),
  );
  await waitFor(() => expect(mocks.resume).toHaveBeenCalledOnce());
  view.unmount();
  await act(async () => {
    finish({ agent_id: "current", thread_id: "continued" });
  });
  expect(continued).not.toHaveBeenCalled();
});
