import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  MemoryRouter,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import MainLayout from ".";
import {
  ConversationNavigationProvider,
  useConversationNavigation,
} from "../ConversationNavigation";
import { useConversationPageVisible } from "../../hooks/useConversationPageVisible";

const mount = vi.hoisted(() => ({ agent: vi.fn(), company: vi.fn() }));
const identity = vi.hoisted(() => ({ id: 1 }));

function Page({ tab }: { tab: "agent" | "company" }) {
  const location = useLocation();
  const params = useParams();
  const visible = useConversationPageVisible();
  const [draft, setDraft] = useState("");
  useEffect(() => {
    mount[tab]();
  }, [tab]);
  return (
    <section data-testid={tab}>
      <input
        aria-label={`${tab} draft`}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      <output data-testid={`${tab}-route`}>
        {JSON.stringify({
          path: location.pathname + location.search,
          params,
          state: location.state,
          visible,
        })}
      </output>
    </section>
  );
}

vi.mock("../../pages/Chat", () => ({ default: () => <Page tab="agent" /> }));
vi.mock("../../pages/Contacts", () => ({
  default: () => <Page tab="company" />,
}));
vi.mock("../Sidebar", () => ({
  default: function SidebarNavigation() {
    const navigate = useNavigate();
    const conversationPath = useConversationNavigation();
    return (
      <nav>
        <button onClick={() => navigate(conversationPath("/contacts"))}>
          Company
        </button>
        <button onClick={() => navigate(conversationPath("/chat"))}>
          Agent
        </button>
      </nav>
    );
  },
}));
vi.mock("../Header", () => ({ default: () => null }));
vi.mock("../../components/RailEdgeControl", () => ({ default: () => null }));
vi.mock("../../components/PrivateExpertBoundary", () => ({
  default: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("../../components/PwaUpdatePrompt", () => ({ default: () => null }));
vi.mock("../../components/PwaInstallPrompt", () => ({
  PwaAutoPrompt: () => null,
}));
vi.mock("../../context/ServiceRestartContext", () => ({
  ServiceRestartProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("../../context/BackupOperationContext", () => ({
  BackupOperationProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../hooks/useKeyboardOffset", () => ({
  useKeyboardOffset: () => undefined,
}));
vi.mock("../../hooks/useDashboardPushToast", () => ({
  useDashboardPushToast: () => undefined,
}));
vi.mock("../../context/LayoutModeContext", () => ({
  useLayoutMode: () => ({ layoutMode: "classic" }),
}));
vi.mock("../../pages/Chat/hooks/useChatSidebarState", () => ({
  useChatSidebarOpen: () => [true, vi.fn()],
}));
vi.mock("../../pages/Chat/components/ChatSidebarPanel", () => ({
  EXPAND_CHAT_RAIL_EVENT: "expand-chat",
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: identity.id, role: "admin" }),
}));
vi.mock("../../pages/Messaging/hooks/CommunicationContext", () => ({
  useCommunication: () => ({
    state: { directoryName: "Company", conversations: {} },
  }),
}));

function Navigation() {
  const navigate = useNavigate();
  return (
    <>
      <button
        onClick={() =>
          navigate("/chat/second/thread-two", { state: { newChat: true } })
        }
      >
        Second thread
      </button>
      <button onClick={() => navigate("/contacts?department=engineering")}>
        Department
      </button>
      <button onClick={() => navigate("/messages/colleague")}>Colleague</button>
      <button onClick={() => navigate(-1)}>Back</button>
    </>
  );
}

function Workspace({ path = "/chat/expert/thread-one" }: { path?: string }) {
  return (
    <MemoryRouter initialEntries={[path]}>
      <ConversationNavigationProvider>
        <Navigation />
        <MainLayout />
      </ConversationNavigationProvider>
    </MemoryRouter>
  );
}

beforeEach(() => {
  mount.agent.mockClear();
  mount.company.mockClear();
  identity.id = 1;
});

it("keeps both pages and drafts mounted while switching Agent/company repeatedly", async () => {
  render(<Workspace />);
  const agent = await screen.findByTestId("agent");
  const draft = screen.getByLabelText("agent draft");
  fireEvent.change(draft, { target: { value: "Unsent agent message" } });
  fireEvent.click(screen.getByRole("button", { name: "Company" }));
  const company = await screen.findByTestId("company");
  fireEvent.change(screen.getByLabelText("company draft"), {
    target: { value: "Unsent company message" },
  });

  for (let i = 0; i < 3; i++) {
    fireEvent.click(screen.getByRole("button", { name: "Agent" }));
    expect(screen.getByTestId("agent")).toBe(agent);
    expect(agent).toBeVisible();
    expect(company).not.toBeVisible();
    expect(screen.getByLabelText("agent draft")).toHaveValue(
      "Unsent agent message",
    );
    fireEvent.click(screen.getByRole("button", { name: "Company" }));
    expect(screen.getByTestId("company")).toBe(company);
    expect(company).toBeVisible();
    expect(agent).not.toBeVisible();
    expect(screen.getByLabelText("company draft")).toHaveValue(
      "Unsent company message",
    );
  }
  expect(mount.agent).toHaveBeenCalledTimes(1);
  expect(mount.company).toHaveBeenCalledTimes(1);
});

it("freezes hidden route params and restores department queries and browser history", async () => {
  render(<Workspace />);
  const agent = await screen.findByTestId("agent");
  fireEvent.click(screen.getByText("Second thread"));
  expect(screen.getByTestId("agent")).toBe(agent);
  fireEvent.click(screen.getByText("Department"));
  const company = await screen.findByTestId("company");
  expect(JSON.parse(screen.getByTestId("agent-route").textContent!)).toEqual({
    path: "/chat/second/thread-two",
    params: { agentId: "second", threadId: "thread-two" },
    state: { newChat: true },
    visible: false,
  });
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));
  expect(
    JSON.parse(screen.getByTestId("company-route").textContent!).path,
  ).toBe("/contacts?department=engineering");
  fireEvent.click(screen.getByRole("button", { name: "Company" }));
  expect(screen.getByTestId("company")).toBe(company);
  fireEvent.click(screen.getByText("Colleague"));
  expect(screen.getByTestId("company")).toBe(company);
  fireEvent.click(screen.getByText("Back"));
  await waitFor(() =>
    expect(
      JSON.parse(screen.getByTestId("company-route").textContent!).path,
    ).toBe("/contacts?department=engineering"),
  );
});

it("mounts only the visited tab and releases cached pages when the account changes", async () => {
  const view = render(<Workspace path="/messages/colleague" />);
  await screen.findByTestId("company");
  expect(mount.agent).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));
  const first = await screen.findByTestId("agent");
  identity.id = 2;
  view.rerender(<Workspace path="/messages/colleague" />);
  expect(screen.getByTestId("agent")).not.toBe(first);
  expect(screen.queryByTestId("company")).toBeNull();
});
