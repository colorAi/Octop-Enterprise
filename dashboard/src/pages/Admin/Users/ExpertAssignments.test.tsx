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
import {
  expertAssignmentsApi,
  type ExpertAssignmentOverview,
} from "../../../api/modules/expertAssignments";
import { ExpertAssignmentDrawer } from "./ExpertAssignments";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      key.startsWith("expertAccess.errors.") ? `translated:${key}` : key,
  }),
}));

const data: ExpertAssignmentOverview = {
  source: { source_user_id: 1, revision: 3 },
  administrators: [{ id: 1, username: "admin", display_name: null }],
  experts: [
    {
      agent_id: "first",
      name: "Finance expert",
      description: "Financial analysis",
    },
    {
      agent_id: "second",
      name: "Writing expert",
      description: "Write documents",
    },
  ],
  pools: [
    {
      scope_id: "unassigned",
      revision: 1,
      expert_ids: ["first", "second"],
      skill_ids: [],
    },
  ],
  skills: [],
  defaults: [],
  employees: [
    {
      id: 2,
      username: "employee",
      display_name: "Employee",
      disabled: false,
      communication_user_id: "employee-im",
      mode: "assigned_only",
      revision: 7,
      expert_ids: ["first"],
    },
  ],
};

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.spyOn(expertAssignmentsApi, "employees").mockResolvedValue({
    employees: [
      {
        id: 2,
        mode: "assigned_only",
        revision: 8,
        expert_ids: [],
        username: "employee",
        display_name: "Employee",
        disabled: false,
        communication_user_id: "employee-im",
      },
    ],
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function mount(
  onSaved = vi.fn(async () => undefined),
  employee = data.employees[0],
  overview = data,
) {
  const close = vi.fn();
  render(
    <App>
      <ExpertAssignmentDrawer
        employee={employee}
        data={overview}
        close={close}
        onSaved={onSaved}
      />
    </App>,
  );
  return { close, onSaved };
}

it("clears only expert assignments with the revisions captured when the drawer opened", async () => {
  const { close } = mount();
  fireEvent.click(screen.getByRole("button", { name: "expertAccess.clear" }));
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(expertAssignmentsApi.employees).toHaveBeenCalledWith(
    [
      {
        id: 2,
        skill_ids: [],
        scope_id: "unassigned",
        membership_revision: 0,
        mode: "assigned_only",
        expert_ids: [],
        expected_revision: 7,
      },
    ],
    3,
  );
});

const skillData: ExpertAssignmentOverview = {
  ...data,
  experts: data.experts.map((e, index) => ({
    ...e,
    skill_ids:
      index === 0 ? ["default", "spreadsheet"] : ["default", "writing"],
  })),
  defaults: ["default"],
  skills: [
    {
      id: "default",
      slug: "default",
      name: "Reading",
      description: "Read files",
      category: "Documents",
      kind: "builtin",
      source_name: "builtin",
      expert_ids: ["first", "second"],
    },
    {
      id: "spreadsheet",
      slug: "spreadsheet",
      name: "Spreadsheet",
      description: "Analyze data",
      category: "Data",
      kind: "workspace",
      source_name: "Finance expert",
      expert_ids: ["first"],
    },
    {
      id: "writing",
      slug: "writing",
      name: "Writing skill",
      description: "Draft content",
      category: "Documents",
      kind: "workspace",
      source_name: "Writing expert",
      expert_ids: ["second"],
    },
    {
      id: "pdf",
      slug: "pdf",
      name: "PDF extraction",
      description: "Read PDFs",
      category: "Documents",
      kind: "package",
      source_name: "Package",
      expert_ids: [],
    },
    {
      id: "chart",
      slug: "chart",
      name: "Chart",
      description: "Build charts",
      category: "Data",
      kind: "package",
      source_name: "Package",
      expert_ids: [],
    },
  ],
  pools: [
    {
      scope_id: "unassigned",
      revision: 1,
      expert_ids: ["first", "second"],
      skill_ids: ["pdf", "chart"],
    },
  ],
};

it("shows all three settings tabs and updates expert-provided skills from the unsaved expert draft", async () => {
  mount(undefined, data.employees[0], skillData);
  expect(
    screen.getByRole("tab", { name: "employeeResources.tabs.basic" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Finance expert" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Writing expert" }));
  fireEvent.click(
    screen.getByRole("tab", { name: "employeeResources.tabs.skills" }),
  );
  expect(screen.getByText("Reading")).toBeInTheDocument();
  expect(screen.getByText("Writing skill")).toBeInTheDocument();
  expect(screen.queryByText("Spreadsheet")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("checkbox", { name: "Reading" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("checkbox", { name: "PDF extraction" }),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("tab", { name: "employeeResources.tabs.basic" }),
  );
  expect(screen.getByDisplayValue("Employee")).toHaveAttribute("readonly");
  expect(expertAssignmentsApi.employees).not.toHaveBeenCalled();
});

it("keeps selected extra skills hidden by category filters and preserves the expert selection", async () => {
  mount(undefined, { ...data.employees[0], skill_ids: ["pdf"] }, skillData);
  fireEvent.click(
    screen.getByRole("tab", { name: "employeeResources.tabs.skills" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Data", exact: true }));
  expect(
    screen.queryByRole("checkbox", { name: "PDF extraction" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Chart" }));
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await waitFor(() =>
    expect(expertAssignmentsApi.employees).toHaveBeenCalledOnce(),
  );
  expect(
    vi.mocked(expertAssignmentsApi.employees).mock.calls[0][0][0],
  ).toMatchObject({ expert_ids: ["first"], skill_ids: ["pdf", "chart"] });
});

it("lets administrators remove disabled employees' extra skills without granting new ones", async () => {
  const { close } = mount(
    undefined,
    {
      ...data.employees[0],
      expert_ids: [],
      skill_ids: ["pdf"],
      disabled: true,
    },
    skillData,
  );
  fireEvent.click(
    screen.getByRole("tab", { name: "employeeResources.tabs.skills" }),
  );
  expect(screen.getByRole("checkbox", { name: "Chart" })).toBeDisabled();
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.clearExtras" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(
    vi.mocked(expertAssignmentsApi.employees).mock.calls[0][0][0].skill_ids,
  ).toEqual([]);
});

it("preserves selected experts hidden by the search filter", async () => {
  mount();
  fireEvent.change(screen.getByLabelText("expertAccess.search"), {
    target: { value: "Writing" },
  });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await waitFor(() =>
    expect(expertAssignmentsApi.employees).toHaveBeenCalledOnce(),
  );
  expect(expertAssignmentsApi.employees).toHaveBeenCalledWith(
    [
      {
        id: 2,
        skill_ids: [],
        scope_id: "unassigned",
        membership_revision: 0,
        mode: "assigned_only",
        expert_ids: ["first", "second"],
        expected_revision: 7,
      },
    ],
    3,
  );
});

it("keeps the draft open on a revision conflict and explains that it must be refreshed", async () => {
  vi.mocked(expertAssignmentsApi.employees).mockRejectedValue(
    new Error(
      JSON.stringify({
        error: {
          code: "FORBIDDEN",
          details: { reason: "policy_revision_conflict" },
        },
      }),
    ),
  );
  const { close } = mount();
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  expect(
    await screen.findByText(
      "translated:expertAccess.errors.policy_revision_conflict",
    ),
  ).toBeInTheDocument();
  expect(close).not.toHaveBeenCalled();
  expect(screen.getAllByRole("checkbox")[0]).toBeChecked();
});

it("does not offer to resubmit a committed assignment when refreshing the list fails", async () => {
  const { close } = mount(
    vi.fn(async () => {
      throw new Error("offline");
    }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  expect(
    await screen.findByText("expertAccess.savedRefreshFailed"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  ).toBeDisabled();
  expect(close).not.toHaveBeenCalled();
  await act(async () =>
    fireEvent.click(
      screen.getByRole("button", { name: "employeeResources.saveSettings" }),
    ),
  );
  expect(expertAssignmentsApi.employees).toHaveBeenCalledOnce();
});

it("allows revoking disabled employees without offering them additional experts", async () => {
  const { close } = mount(undefined, { ...data.employees[0], disabled: true });
  expect(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  ).toBeDisabled();
  expect(
    screen.getByRole("checkbox", { name: "Writing expert" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "expertAccess.clear" }));
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(expertAssignmentsApi.employees).toHaveBeenCalledWith(
    [
      {
        id: 2,
        skill_ids: [],
        scope_id: "unassigned",
        membership_revision: 0,
        mode: "assigned_only",
        expert_ids: [],
        expected_revision: 7,
      },
    ],
    3,
  );
});

it("refreshes partial basic saves and prevents submitting the stale form again", async () => {
  vi.mocked(expertAssignmentsApi.employees).mockRejectedValue(
    new Error(
      JSON.stringify({
        error: {
          code: "FORBIDDEN",
          details: { reason: "basic_settings_partial", refresh_required: true },
        },
      }),
    ),
  );
  const { close, onSaved } = mount();
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  expect(
    screen.getByText("employeeResources.basicSavedResourceFailed"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  ).toBeDisabled();
  expect(close).not.toHaveBeenCalled();
});
