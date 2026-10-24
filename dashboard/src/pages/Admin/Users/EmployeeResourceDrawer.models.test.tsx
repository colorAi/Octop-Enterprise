import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, expect, it, vi } from "vitest";
import { usersApi, type EmployeeModelAccess } from "../../../api/modules/users";
import {
  expertAssignmentsApi,
  type ExpertAssignmentOverview,
} from "../../../api/modules/expertAssignments";
import EmployeeResourceDrawer from "./EmployeeResourceDrawer";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

const access: EmployeeModelAccess = {
  model_refs: [],
  revision: "rev-1",
  items: [
    { ref: "alpha/chat", provider_name: "alpha", model: "chat", name: "Chat" },
  ],
};
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

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function show() {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.spyOn(usersApi, "models").mockResolvedValue(access);
  const experts = vi.spyOn(expertAssignmentsApi, "employees");
  const basic = vi.spyOn(expertAssignmentsApi, "basic");
  const knowledge = vi.spyOn(usersApi, "setKnowledgeBases");
  const close = vi.fn();
  render(
    <App>
      <EmployeeResourceDrawer
        employee={data.employees[0]}
        data={data}
        initialTab="models"
        close={close}
        onSaved={async () => undefined}
      />
    </App>,
  );
  return { experts, basic, knowledge, close };
}

it("saves only model grants without changing legacy expert mode or other employee resources", async () => {
  const save = vi.spyOn(usersApi, "setModels").mockResolvedValue({
    ...access,
    model_refs: ["alpha/chat"],
    revision: "rev-2",
  });
  const { experts, basic, knowledge, close } = show();
  const button = screen.getByRole("button", {
    name: "employeeResources.saveSettings",
  });
  expect(button).toBeDisabled();
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "alpha / Chat" }),
  );
  expect(screen.queryByText("expertAccess.selectSourceFirst")).toBeNull();
  fireEvent.click(button);
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(save).toHaveBeenCalledWith(2, {
    model_refs: ["alpha/chat"],
    expected_revision: "rev-1",
  });
  expect(experts).not.toHaveBeenCalled();
  expect(basic).not.toHaveBeenCalled();
  expect(knowledge).not.toHaveBeenCalled();
});

it("retains the draft and keeps the drawer open when saving fails", async () => {
  vi.spyOn(usersApi, "setModels").mockRejectedValue(
    new Error("assignments changed; refresh before saving"),
  );
  const { close } = show();
  const model = await screen.findByRole("checkbox", { name: "alpha / Chat" });
  fireEvent.click(model);
  fireEvent.click(
    screen.getByRole("button", { name: "employeeResources.saveSettings" }),
  );
  await screen.findByText("assignments changed; refresh before saving");
  expect(close).not.toHaveBeenCalled();
  expect(model).toBeChecked();
});
