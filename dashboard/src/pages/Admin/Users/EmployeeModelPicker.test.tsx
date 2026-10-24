import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { usersApi, type EmployeeModelAccess } from "../../../api/modules/users";
import EmployeeModelPicker from "./EmployeeModelPicker";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

const catalog: EmployeeModelAccess = {
  model_refs: ["alpha/chat"],
  revision: "rev-1",
  items: [
    { ref: "alpha/chat", provider_name: "alpha", model: "chat", name: "Chat" },
    { ref: "beta/chat", provider_name: "beta", model: "chat", name: "Chat" },
  ],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("loads without changing grants and sends a revision only after editing", async () => {
  vi.spyOn(usersApi, "models").mockResolvedValue(catalog);
  const onChange = vi.fn();
  render(<EmployeeModelPicker userId={2} onChange={onChange} />);
  const alpha = await screen.findByRole("checkbox", { name: "alpha / Chat" });
  const beta = screen.getByRole("checkbox", { name: "beta / Chat" });
  expect(alpha).toBeChecked();
  expect(beta).not.toBeChecked();
  expect(onChange).toHaveBeenLastCalledWith(null);
  fireEvent.click(beta);
  expect(onChange).toHaveBeenLastCalledWith({
    model_refs: ["alpha/chat", "beta/chat"],
    expected_revision: "rev-1",
  });
  fireEvent.click(beta);
  expect(onChange).toHaveBeenLastCalledWith(null);
  fireEvent.click(screen.getByRole("button", { name: "expertAccess.clear" }));
  expect(onChange).toHaveBeenLastCalledWith({
    model_refs: [],
    expected_revision: "rev-1",
  });
  expect(screen.getByText("employeeModels.noneAssigned")).toBeVisible();
});

it("keeps stale grants visible for removal without silently clearing them", async () => {
  vi.spyOn(usersApi, "models").mockResolvedValue({
    ...catalog,
    model_refs: ["removed/old", "alpha/chat"],
  });
  const onChange = vi.fn();
  render(<EmployeeModelPicker userId={2} onChange={onChange} />);
  await screen.findByText("removed/old");
  expect(onChange).toHaveBeenLastCalledWith(null);
  fireEvent.click(
    screen.getByRole("button", { name: "expertAccess.removeUnavailable" }),
  );
  expect(onChange).toHaveBeenLastCalledWith({
    model_refs: ["alpha/chat"],
    expected_revision: "rev-1",
  });
});

it("ignores late responses for the previous employee", async () => {
  let complete!: (value: EmployeeModelAccess) => void;
  const load = vi
    .spyOn(usersApi, "models")
    .mockReturnValueOnce(
      new Promise((resolve) => {
        complete = resolve;
      }),
    )
    .mockResolvedValueOnce({ ...catalog, model_refs: [], revision: "rev-2" });
  const onChange = vi.fn();
  const view = render(<EmployeeModelPicker userId={2} onChange={onChange} />);
  view.rerender(<EmployeeModelPicker userId={3} onChange={onChange} />);
  const alpha = await screen.findByRole("checkbox", { name: "alpha / Chat" });
  expect(load.mock.calls[0][1]?.aborted).toBe(true);
  await act(async () => complete(catalog));
  expect(alpha).not.toBeChecked();
  fireEvent.click(alpha);
  expect(onChange).toHaveBeenLastCalledWith({
    model_refs: ["alpha/chat"],
    expected_revision: "rev-2",
  });
});

it("leaves existing assignments untouched on load failure and supports retry", async () => {
  vi.spyOn(usersApi, "models")
    .mockRejectedValueOnce(new Error("catalog offline"))
    .mockResolvedValueOnce(catalog);
  const onChange = vi.fn();
  render(<EmployeeModelPicker userId={2} onChange={onChange} />);
  await screen.findByText("catalog offline");
  expect(onChange).toHaveBeenLastCalledWith(null);
  fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
  expect(
    await screen.findByRole("checkbox", { name: "alpha / Chat" }),
  ).toBeChecked();
  expect(onChange).toHaveBeenLastCalledWith(null);
});
