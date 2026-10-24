import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, expect, it, vi } from "vitest";
import { usersApi } from "../../../api/modules/users";
import {
  expertAssignmentsApi,
  type ExpertAssignmentOverview,
} from "../../../api/modules/expertAssignments";
import EmployeeResourceDrawer from "./EmployeeResourceDrawer";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("saves knowledge access without changing experts, skills or department settings", async () => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  const access = {
    knowledge_base_ids: [],
    items: [
      {
        id: "procedures",
        name: "Company procedures",
        description: "",
        shared: false,
      },
    ],
  };
  vi.spyOn(usersApi, "knowledgeBases").mockResolvedValue(access);
  const save = vi
    .spyOn(usersApi, "setKnowledgeBases")
    .mockResolvedValue(access);
  const experts = vi.spyOn(expertAssignmentsApi, "employees");
  const basic = vi.spyOn(expertAssignmentsApi, "basic");
  const close = vi.fn();
  const data: ExpertAssignmentOverview = {
    source: { source_user_id: null, revision: 0 },
    administrators: [],
    experts: [],
    employees: [
      {
        id: 2,
        username: "Alice",
        display_name: null,
        disabled: false,
        communication_user_id: null,
        mode: "legacy",
        revision: 0,
        expert_ids: [],
      },
    ],
  };
  render(
    <App>
      <EmployeeResourceDrawer
        employee={data.employees[0]}
        data={data}
        initialTab="experts"
        close={close}
        onSaved={vi.fn()}
      />
    </App>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "employeeKnowledge.tab" }));
  expect(screen.queryByText("expertAccess.selectSourceFirst")).toBeNull();
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "Company procedures" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(save).toHaveBeenCalledWith(2, ["procedures"]);
  expect(experts).not.toHaveBeenCalled();
  expect(basic).not.toHaveBeenCalled();
});

it.each(["knowledge", "experts"])(
  "preserves drafts across tabs when saving from %s",
  async (saveTab) => {
    const getComputedStyle = window.getComputedStyle.bind(window);
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
      getComputedStyle(element),
    );
    const access = {
      knowledge_base_ids: [],
      items: [
        {
          id: "procedures",
          name: "Company procedures",
          description: "",
          shared: false,
        },
      ],
    };
    vi.spyOn(usersApi, "knowledgeBases").mockResolvedValue(access);
    const saveKnowledge = vi
      .spyOn(usersApi, "setKnowledgeBases")
      .mockResolvedValue(access);
    const saveExperts = vi
      .spyOn(expertAssignmentsApi, "employees")
      .mockResolvedValue({ employees: [] });
    const close = vi.fn();
    const data: ExpertAssignmentOverview = {
      source: { source_user_id: 1, revision: 2 },
      administrators: [],
      experts: [{ agent_id: "writer", name: "Writer" }],
      employees: [
        {
          id: 2,
          username: "Alice",
          display_name: null,
          disabled: false,
          communication_user_id: null,
          mode: "assigned_only",
          revision: 1,
          expert_ids: [],
        },
      ],
      pools: [
        {
          scope_id: "unassigned",
          revision: 1,
          expert_ids: ["writer"],
          skill_ids: [],
        },
      ],
    };
    render(
      <App>
        <EmployeeResourceDrawer
          employee={data.employees[0]}
          data={data}
          initialTab="knowledge"
          close={close}
          onSaved={async () => undefined}
        />
      </App>,
    );
    fireEvent.click(
      await screen.findByRole("checkbox", { name: "Company procedures" }),
    );
    fireEvent.click(
      screen.getByRole("tab", { name: "employeeResources.tabs.experts" }),
    );
    fireEvent.click(await screen.findByRole("checkbox", { name: "Writer" }));
    if (saveTab === "knowledge")
      fireEvent.click(
        screen.getByRole("tab", { name: "employeeKnowledge.tab" }),
      );
    fireEvent.click(
      screen.getByRole("button", { name: "employeeResources.saveSettings" }),
    );
    await waitFor(() => expect(close).toHaveBeenCalledOnce());
    expect(saveKnowledge).toHaveBeenCalledWith(2, ["procedures"]);
    expect(saveExperts).toHaveBeenCalledWith(
      [
        expect.objectContaining({
          id: 2,
          expert_ids: ["writer"],
          mode: "assigned_only",
        }),
      ],
      2,
    );
  },
);
