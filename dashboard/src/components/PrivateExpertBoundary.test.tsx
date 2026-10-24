import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";

const state = vi.hoisted(() => ({
  create: vi.fn(),
  refresh: vi.fn(),
  select: vi.fn(),
  navigate: vi.fn(),
  visible: true,
  t: (key: string) => key,
}));
vi.mock("../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [
      {
        agent_id: "source",
        name: "Assigned expert",
        requires_private_instance: true,
      },
      { agent_id: "other", name: "Another expert" },
    ],
    activeAgentId: "source",
    loading: false,
    refresh: state.refresh,
    setActiveAgent: state.select,
  }),
}));
vi.mock("../api/modules/octopAgents", () => ({
  octopAgentsApi: { privateInstance: state.create },
}));
vi.mock("react-router-dom", () => ({
  useNavigate: () => state.navigate,
  useParams: () => ({ agentId: "source" }),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: state.t }) }));
vi.mock("../hooks/useConversationPageVisible", () => ({
  useConversationPageVisible: () => state.visible,
}));
vi.mock("./PageLoading", () => ({ default: () => <div>Loading</div> }));
vi.mock("antd", () => ({
  Result: ({
    title,
    subTitle,
    extra,
  }: {
    title: ReactNode;
    subTitle: ReactNode;
    extra: ReactNode;
  }) => (
    <section>
      <h2>{title}</h2>
      <p>{subTitle}</p>
      {extra}
    </section>
  ),
  Space: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Button: ({
    children,
    onClick,
  }: {
    children: ReactNode;
    onClick: () => void;
  }) => <button onClick={onClick}>{children}</button>,
  Select: ({
    options,
    onChange,
    "aria-label": label,
  }: {
    options: { value: string; label: string }[];
    onChange: (id: string) => void;
    "aria-label": string;
  }) => (
    <select
      aria-label={label}
      defaultValue=""
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="" />
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

import PrivateExpertBoundary from "./PrivateExpertBoundary";

beforeEach(() => {
  vi.clearAllMocks();
  state.visible = true;
  state.refresh.mockResolvedValue(undefined);
});
afterEach(cleanup);

it("keeps retry and expert switching available after initialization fails", async () => {
  state.create.mockRejectedValue(new Error("Runtime temporarily unavailable"));
  render(<PrivateExpertBoundary>Chat</PrivateExpertBoundary>);
  expect(
    await screen.findByText("Runtime temporarily unavailable"),
  ).toBeTruthy();
  expect(screen.getByText("Assigned expert")).toBeTruthy();
  fireEvent.click(screen.getByText("communication.retry"));
  await waitFor(() => expect(state.create).toHaveBeenCalledTimes(2));
  fireEvent.change(screen.getByLabelText("chat.pickAgent"), {
    target: { value: "other" },
  });
  expect(state.select).toHaveBeenCalledWith("other");
  expect(state.navigate).toHaveBeenCalledWith("/chat/other");
});

it("selects the created instance before refresh removes the source picker entry", async () => {
  state.create.mockResolvedValue({ agent_id: "private" });
  state.refresh.mockImplementation(() => new Promise(() => {}));
  render(<PrivateExpertBoundary>Chat</PrivateExpertBoundary>);
  await waitFor(() => expect(state.refresh).toHaveBeenCalled());
  expect(state.select).toHaveBeenCalledWith("private");
  expect(state.navigate).toHaveBeenCalledWith("/chat/private", {
    replace: true,
  });
});

it("does not create an instance for a hidden conversation page", () => {
  state.visible = false;
  render(<PrivateExpertBoundary>Chat</PrivateExpertBoundary>);
  expect(state.create).not.toHaveBeenCalled();
});
