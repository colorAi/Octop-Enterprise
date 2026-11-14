import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import {
  communicationApi,
  CommunicationError,
} from "../../../api/modules/communication";
import DepartmentsPanel from "./DepartmentsPanel";

const fixture = vi.hoisted(() => ({
  t: (key: string) => key,
  state: { profile: { user_id: "administrator" } },
  client: { synchronize: vi.fn(async () => undefined) },
  department: {
    department_id: "department",
    name: "Production",
    parent_id: null,
    enabled: true,
    sort_order: 0,
    member_count: 1,
    conversation_id: null as string | null,
  },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: fixture.t }),
}));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 1, role: "admin" }),
}));
vi.mock("../../Messaging/hooks/CommunicationContext", () => ({
  useCommunication: () => fixture,
}));

const org = communicationApi.organization;
beforeEach(() => {
  vi.restoreAllMocks();
  fixture.department.conversation_id = null;
  fixture.client.synchronize.mockReset().mockResolvedValue(undefined);
  vi.spyOn(org, "departments").mockImplementation(async () => ({
    items: [{ ...fixture.department }],
  }));
  vi.spyOn(org, "users").mockResolvedValue({ items: [] });
  vi.spyOn(org, "directorySettings").mockResolvedValue({ name: "Company" });
  vi.spyOn(org, "jobTitles").mockResolvedValue({ items: [] });
  vi.spyOn(communicationApi, "groupExperts").mockResolvedValue({
    items: [],
    candidates: [],
    scope_sources: [],
    scope_revision: 0,
    experts_enabled: false,
    ready: true,
    syncing: false,
    can_manage: true,
    can_configure: true,
    status: "active",
    group_kind: "department",
    department_id: "department",
  });
  vi.spyOn(org, "createGroup").mockImplementation(async () => {
    fixture.department.conversation_id = "group";
    return { conversation_id: "group" };
  });
  vi.spyOn(org, "group").mockResolvedValue({
    name: "Production group",
    announcement: null,
    owner_user_id: "employee",
    mute_all: false,
    members: [{ user_id: "employee", nickname: "Employee", role: "owner" }],
  });
});

function mount() {
  return render(
    <App>
      <MemoryRouter initialEntries={["/admin"]}>
        <Routes>
          <Route path="/admin" element={<DepartmentsPanel />} />
          <Route path="/messages/group" element={<p>Opened group chat</p>} />
        </Routes>
      </MemoryRouter>
    </App>,
  );
}

it("opens company and project management without joining a group", async () => {
  vi.spyOn(communicationApi, "managedGroups").mockResolvedValue({
    items: [
      {
        conversation_id: "company-group",
        name: "Company group",
        group_kind: "company",
        status: "active",
        is_member: false,
        announcement: null,
        owner_user_id: "employee",
        mute_all: false,
      },
      {
        conversation_id: "project-group",
        name: "Project group",
        group_kind: "project",
        status: "active",
        is_member: false,
        announcement: null,
        owner_user_id: "employee",
        mute_all: false,
      },
    ],
  });
  const join = vi.spyOn(communicationApi, "groupMembers");
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.manageGroups" }),
  );
  await screen.findByText("Company group");
  await screen.findByText("Project group");
  const controls = screen.getAllByRole("button", {
    name: "communication.manageGroups",
  });
  fireEvent.click(controls[1]);
  await waitFor(() =>
    expect(org.group).toHaveBeenCalledWith(
      "company-group",
      expect.any(AbortSignal),
    ),
  );
  expect(await screen.findByText("groupExperts.title")).toBeInTheDocument();
  expect(join).not.toHaveBeenCalled();
});

it("lets a nonmember administrator configure group experts without joining", async () => {
  fixture.department.conversation_id = "group";
  const enable = vi
    .spyOn(communicationApi, "enableExperts")
    .mockResolvedValue({ experts_enabled: true });
  const join = vi.spyOn(communicationApi, "groupMembers");
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.manageGroup" }),
  );
  const toggle = await screen.findByRole("switch", {
    name: "groupExperts.enable",
  });
  expect(toggle).not.toBeChecked();
  expect(toggle).toBeEnabled();
  expect(communicationApi.groupExperts).toHaveBeenCalledWith("group", true);
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(enable).toHaveBeenCalledWith("group", {
      request_id: expect.any(String),
      enabled: true,
    }),
  );
  expect(join).not.toHaveBeenCalled();
  expect(org.createGroup).not.toHaveBeenCalled();
});

it("acknowledges creation and explains why a managing nonmember has no chat entry", async () => {
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.createGroup" }),
  );
  expect(
    await screen.findByText("communication.departmentGroupCreated"),
  ).toBeInTheDocument();
  expect(
    await screen.findByText("communication.groupManagementOnlyHint"),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "communication.openGroupChat" }),
  ).not.toBeInTheDocument();
  expect(org.createGroup).toHaveBeenCalledTimes(1);
  await waitFor(() => expect(fixture.client.synchronize).toHaveBeenCalled());
  expect(
    screen.getByRole("button", {
      name: "communication.manageGroup",
      hidden: true,
    }),
  ).toBeInTheDocument();
});

it("opens an existing group for a current member without recreating it", async () => {
  fixture.department.conversation_id = "group";
  vi.mocked(org.group).mockResolvedValue({
    name: "Production group",
    announcement: null,
    owner_user_id: "administrator",
    mute_all: false,
    members: [{ user_id: "administrator", nickname: "Admin", role: "owner" }],
  });
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.manageGroup" }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.openGroupChat" }),
  );
  expect(await screen.findByText("Opened group chat")).toBeInTheDocument();
  expect(org.createGroup).not.toHaveBeenCalled();
});

it("surfaces an empty department failure where the action is taken", async () => {
  vi.mocked(org.createGroup).mockRejectedValue(
    new CommunicationError("invalid_state", 409, false, "empty"),
  );
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.createGroup" }),
  );
  await waitFor(() =>
    expect(
      screen.getAllByText("communication.departmentGroupNeedsMembers").length,
    ).toBeGreaterThan(0),
  );
  expect(
    screen.queryByText("communication.departmentGroupCreated"),
  ).not.toBeInTheDocument();
  expect(org.group).not.toHaveBeenCalled();
});

it("retains a successfully created group when subsequent synchronization fails", async () => {
  fixture.client.synchronize.mockRejectedValue(
    new Error("network unavailable"),
  );
  mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.createGroup" }),
  );
  expect(
    await screen.findByText("communication.groupManagementOnlyHint"),
  ).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getAllByText("communication.groupRefreshFailed").length,
    ).toBeGreaterThan(0),
  );
  expect(org.createGroup).toHaveBeenCalledTimes(1);
});
