import { render, screen, act } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ExpertCallStatus from "./ExpertCallStatus";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});
it("uses durable history updates without per-call polling", async () => {
  vi.useFakeTimers();
  const view = render(
    <ExpertCallStatus call={{ call_id: "call", call_state: "dispatched" }} />,
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "groupExperts.states.dispatched",
  );
  await act(async () => {
    vi.advanceTimersByTime(9000);
  });
  expect(vi.getTimerCount()).toBe(0);
  view.rerender(
    <ExpertCallStatus call={{ call_id: "call", call_state: "completed" }} />,
  );
  expect(screen.getByRole("status")).toHaveTextContent(
    "groupExperts.states.completed",
  );
});
