import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import ContactsPage from "./index";
import {
  ConversationNavigationProvider,
  useConversationNavigation,
} from "../../layouts/ConversationNavigation";

const account = vi.hoisted(() => ({
  id: 1,
  phase: "ready",
  presence: { alice: true } as Record<string, true> | null,
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({
    id: account.id,
    role: "user",
    permissions: ["contacts", "messaging"],
  }),
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../hooks/useAuthImageSrc", () => ({
  useAuthImageSrc: (url: string) => ({ src: url, loadState: "ready" }),
}));
vi.mock("../Messaging", () => ({
  ChatPane: () => null,
  ConversationList: () => <div>Recent conversations</div>,
}));
vi.mock("../Messaging/hooks/CommunicationContext", () => ({
  useCommunication: () => ({
    client: { synchronize: vi.fn() },
    state: {
      error: null,
      phase: account.phase,
      realtime: "offline",
      presence: account.presence,
      directoryName: "Test Company",
      profile: { user_id: "self" },
      conversations: {},
      contacts: {
        alice: {
          user_id: "alice",
          nickname: "Alice",
          avatar_url: "/api/users/2/avatar?v=123",
          avatar_icon: "business",
          job_title: "产品经理",
          department_id: "engineering",
        },
      },
      departments: [
        {
          department_id: "company",
          name: "Company",
          parent_id: null,
          sort_order: 0,
        },
        {
          department_id: "engineering",
          name: "Engineering",
          parent_id: "company",
          sort_order: 0,
        },
      ],
    },
  }),
}));

function Navigation() {
  const navigate = useNavigate();
  const conversationPath = useConversationNavigation();
  return (
    <nav>
      <button onClick={() => navigate(conversationPath("/contacts"))}>
        Company
      </button>
      <button onClick={() => navigate(conversationPath("/chat"))}>Agent</button>
    </nav>
  );
}

function Workspace() {
  return (
    <MemoryRouter initialEntries={["/contacts"]}>
      <ConversationNavigationProvider>
        <Navigation />
        <Routes>
          <Route path="/contacts" element={<ContactsPage />} />
          <Route path="/chat" element={null} />
        </Routes>
      </ConversationNavigationProvider>
    </MemoryRouter>
  );
}

function tree() {
  return within(screen.getByRole("tree"));
}
function toggleDepartment(name: string) {
  const row = tree().getByText(name).closest(".ant-tree-treenode")!;
  fireEvent.click(row.querySelector(".ant-tree-switcher")!);
}
async function employeeHidden() {
  await waitFor(() =>
    expect(tree().queryByText("Alice")).not.toBeInTheDocument(),
  );
}

beforeEach(() => {
  account.id = 1;
  account.phase = "ready";
  account.presence = { alice: true };
  localStorage.clear();
});

it("shows the account portrait and company position on the contact profile", () => {
  render(
    <MemoryRouter initialEntries={["/contacts/alice"]}>
      <Routes>
        <Route path="/contacts/:personId" element={<ContactsPage />} />
      </Routes>
    </MemoryRouter>,
  );
  const profile = screen.getByRole("heading", { name: "Alice" }).parentElement!;
  expect(profile.querySelector("img")).toHaveAttribute(
    "src",
    "/api/users/2/avatar?v=123",
  );
  expect(within(profile).getByText("产品经理")).toBeInTheDocument();
  expect(
    within(profile).getByText("communication.presence.online"),
  ).toBeInTheDocument();
});

it("updates directory dots and profile labels for offline and unknown states", () => {
  const page = () => (
    <MemoryRouter initialEntries={["/contacts/alice"]}>
      <Routes>
        <Route path="/contacts/:personId" element={<ContactsPage />} />
      </Routes>
    </MemoryRouter>
  );
  const view = render(page());
  expect(
    tree().getByTitle("communication.presence.online"),
  ).toBeInTheDocument();
  account.presence = {};
  view.rerender(page());
  expect(
    tree().getByTitle("communication.presence.offline"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("communication.presence.offline"),
  ).toBeInTheDocument();
  account.presence = null;
  view.rerender(page());
  expect(
    tree().queryByTitle("communication.presence.offline"),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText("communication.presence.unknown"),
  ).toBeInTheDocument();
});

it("keeps a collapsed department through Agent switching, recent view, and page remount", async () => {
  const view = render(<Workspace />);
  expect(tree().getByText("Alice")).toBeInTheDocument();
  toggleDepartment("Engineering");
  await employeeHidden();
  fireEvent.click(screen.getByRole("button", { name: "Agent" }));
  fireEvent.click(screen.getByRole("button", { name: "Company" }));
  await employeeHidden();
  fireEvent.click(screen.getByText("communication.recentMessages"));
  expect(screen.getByText("Recent conversations")).toBeInTheDocument();
  fireEvent.click(screen.getByText("communication.organizationTree"));
  await employeeHidden();
  account.phase = "loading";
  view.rerender(<Workspace />);
  expect(screen.queryByRole("tree")).not.toBeInTheDocument();
  account.phase = "ready";
  view.rerender(<Workspace />);
  await employeeHidden();
  view.unmount();
  render(<Workspace />);
  await employeeHidden();
  toggleDepartment("Engineering");
  expect(await tree().findByText("Alice")).toBeInTheDocument();
});

it("temporarily reveals search matches and restores collapsed departments when cleared", async () => {
  render(<Workspace />);
  toggleDepartment("Engineering");
  await employeeHidden();
  const search = screen.getByPlaceholderText("communication.searchDirectory");
  fireEvent.change(search, { target: { value: "Alice" } });
  expect(await tree().findByText("Alice")).toBeInTheDocument();
  toggleDepartment("Engineering");
  await employeeHidden();
  fireEvent.change(search, { target: { value: "" } });
  await employeeHidden();
  toggleDepartment("Engineering");
  expect(await tree().findByText("Alice")).toBeInTheDocument();
  fireEvent.change(search, { target: { value: "Alice" } });
  toggleDepartment("Company");
  await employeeHidden();
  fireEvent.change(search, { target: { value: "" } });
  expect(await tree().findByText("Alice")).toBeInTheDocument();
});

it("keeps collapse choices separate for different company accounts", async () => {
  const first = render(<Workspace />);
  toggleDepartment("Engineering");
  await employeeHidden();
  first.unmount();
  account.id = 2;
  const second = render(<Workspace />);
  expect(tree().getByText("Alice")).toBeInTheDocument();
  second.unmount();
  account.id = 1;
  render(<Workspace />);
  await employeeHidden();
});
