import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import {
  usersApi,
  type EmployeeKnowledgeAccess,
} from "../../../api/modules/users";
import EmployeeKnowledgePicker from "./EmployeeKnowledgePicker";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

const catalog: EmployeeKnowledgeAccess = {
  knowledge_base_ids: ["procedures"],
  items: [
    {
      id: "procedures",
      name: "Company procedures",
      description: "Workflows",
      shared: false,
    },
    {
      id: "finance",
      name: "Finance",
      description: "Expense policy",
      shared: false,
    },
    { id: "welcome", name: "Welcome", description: "All staff", shared: true },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows assigned bases and keeps global sharing read-only while editing explicit grants", async () => {
  vi.spyOn(usersApi, "knowledgeBases").mockResolvedValue(catalog);
  const onChange = vi.fn();
  render(<EmployeeKnowledgePicker userId={2} onChange={onChange} />);
  const procedures = await screen.findByRole("checkbox", {
    name: "Company procedures",
  });
  expect(procedures).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "Finance" })).not.toBeChecked();
  const shared = screen.getByRole("checkbox", { name: "Welcome" });
  expect(shared).toBeChecked();
  expect(shared).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox", { name: "Finance" }));
  expect(onChange).toHaveBeenLastCalledWith(["procedures", "finance"]);
  fireEvent.click(screen.getByRole("button", { name: "expertAccess.clear" }));
  expect(onChange).toHaveBeenLastCalledWith([]);
  expect(shared).toBeChecked();
});

it("ignores a previous employee's late response", async () => {
  let complete!: (value: EmployeeKnowledgeAccess) => void;
  const load = vi
    .spyOn(usersApi, "knowledgeBases")
    .mockReturnValueOnce(
      new Promise((resolve) => {
        complete = resolve;
      }),
    )
    .mockResolvedValueOnce({
      knowledge_base_ids: [],
      items: [catalog.items[1]],
    });
  const onChange = vi.fn();
  const view = render(
    <EmployeeKnowledgePicker userId={2} onChange={onChange} />,
  );
  view.rerender(<EmployeeKnowledgePicker userId={3} onChange={onChange} />);
  await screen.findByRole("checkbox", { name: "Finance" });
  expect(load.mock.calls[0][1].aborted).toBe(true);
  await act(async () => complete(catalog));
  expect(
    screen.queryByRole("checkbox", { name: "Company procedures" }),
  ).toBeNull();
  expect(onChange).toHaveBeenLastCalledWith([]);
});

it("leaves grants unchanged when loading fails and allows retry", async () => {
  const load = vi
    .spyOn(usersApi, "knowledgeBases")
    .mockRejectedValueOnce(new Error("catalog offline"))
    .mockResolvedValueOnce(catalog);
  const onChange = vi.fn();
  render(<EmployeeKnowledgePicker userId={2} onChange={onChange} />);
  await screen.findByText("catalog offline");
  expect(onChange).toHaveBeenLastCalledWith(null);
  fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
  await screen.findByRole("checkbox", { name: "Company procedures" });
  await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  expect(onChange).toHaveBeenLastCalledWith(["procedures"]);
});
