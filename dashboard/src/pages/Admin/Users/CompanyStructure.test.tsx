import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { communicationApi } from "../../../api/modules/communication";
import {
  expertAssignmentsApi,
  type ExpertAssignment,
} from "../../../api/modules/expertAssignments";
import type {
  CommunicationUser,
  JobTitle,
} from "../../../api/types/communication";
import CompanyStructure from "./CompanyStructure";

const identity = vi.hoisted(() => ({ id: 1 }));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: identity.id, role: "admin" }),
}));
vi.mock("../../Messaging/hooks/CommunicationContext", () => ({
  useCommunication: () => ({ client: { synchronize: vi.fn() } }),
}));
vi.mock("./EmployeeResourceDrawer", async (original) => ({
  ...(await original<typeof import("./EmployeeResourceDrawer")>()),
  default: ({ employee }: { employee: ExpertAssignment }) => (
    <div data-testid="opened-policy">{employee.revision}</div>
  ),
}));
const person = {
  user_id: "native:2",
  nickname: "Alice",
  status: "active",
  department_id: "D",
  is_admin: false,
} as CommunicationUser;

beforeEach(() => {
  identity.id = 1;
  const computed = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    computed(element),
  );
  vi.spyOn(communicationApi.organization, "departments").mockResolvedValue({
    items: [],
  });
  vi.spyOn(communicationApi.organization, "users").mockResolvedValue({
    items: [person],
  });
  vi.spyOn(
    communicationApi.organization,
    "directorySettings",
  ).mockResolvedValue({ name: "Company" });
  vi.spyOn(communicationApi.organization, "jobTitles").mockResolvedValue({
    items: [],
  });
  vi.spyOn(expertAssignmentsApi, "directory").mockResolvedValue({
    source: { source_user_id: 1, revision: 1 },
    administrators: [],
    experts: [],
    employees: [],
  });
  vi.spyOn(expertAssignmentsApi, "catalog").mockResolvedValue({
    source: { source_user_id: 1, revision: 1 },
    experts: [],
    skills: [],
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
const mount = () =>
  render(
    <App>
      <CompanyStructure renderGroup={() => null} />
    </App>,
  );

it("opens an editor only after obtaining the employee's authoritative state", async () => {
  const employee: ExpertAssignment = {
    id: 2,
    username: "alice",
    display_name: "Alice",
    disabled: false,
    communication_user_id: "native:2",
    mode: "assigned_only",
    revision: 1,
    expert_ids: [],
    membership_revision: 1,
    scope_id: "D",
    membership_status: "changed",
  };
  vi.mocked(expertAssignmentsApi.directory).mockResolvedValue({
    source: { source_user_id: 1, revision: 1 },
    administrators: [],
    experts: [],
    employees: [employee],
  });
  let resolve!: (value: ExpertAssignment) => void;
  vi.spyOn(expertAssignmentsApi, "employee").mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  mount();
  const open = await screen.findByRole("button", {
    name: "employeeResources.expertManagement",
  });
  await waitFor(() => expect(open).toBeEnabled());
  fireEvent.click(open);
  expect(expertAssignmentsApi.employee).toHaveBeenCalledWith(
    2,
    expect.any(AbortSignal),
  );
  expect(screen.queryByTestId("opened-policy")).toBeNull();
  await act(async () =>
    resolve({ ...employee, revision: 2, membership_status: "current" }),
  );
  expect(screen.getByTestId("opened-policy")).toHaveTextContent("2");
});

it("shows personnel before slow job titles and resources, and retains them after a partial refresh failure", async () => {
  let resolve!: (value: { items: JobTitle[] }) => void;
  vi.mocked(communicationApi.organization.jobTitles).mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  vi.mocked(expertAssignmentsApi.catalog).mockRejectedValue(
    new Error("catalog offline"),
  );
  mount();
  expect(await screen.findByText("Alice")).toBeInTheDocument();
  expect(await screen.findByText("catalog offline")).toBeInTheDocument();
  await act(async () => resolve({ items: [] }));
  vi.mocked(communicationApi.organization.users).mockRejectedValue(
    new Error("directory offline"),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "communication.refresh" }),
  );
  await waitFor(() =>
    expect(communicationApi.organization.users).toHaveBeenCalledTimes(2),
  );
  expect(screen.getByText("Alice")).toBeInTheDocument();
});

it("clears personnel on account changes and ignores the old directory request", async () => {
  let resolve!: (value: { items: CommunicationUser[] }) => void;
  vi.mocked(communicationApi.organization.users)
    .mockReturnValueOnce(
      new Promise((done) => {
        resolve = done;
      }),
    )
    .mockResolvedValueOnce({ items: [{ ...person, nickname: "Bob" }] });
  const view = mount();
  const signal = vi.mocked(communicationApi.organization.users).mock
    .calls[0][0]!;
  identity.id = 3;
  view.rerender(
    <App>
      <CompanyStructure renderGroup={() => null} />
    </App>,
  );
  expect(signal.aborted).toBe(true);
  await screen.findByText("Bob");
  await act(async () => resolve({ items: [person] }));
  expect(screen.queryByText("Alice")).toBeNull();
});
