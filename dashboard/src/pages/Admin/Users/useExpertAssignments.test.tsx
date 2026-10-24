import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  expertAssignmentsApi,
  type ExpertAssignmentOverview,
  type ExpertResourceCatalog,
} from "../../../api/modules/expertAssignments";
import { useExpertAssignments } from "./useExpertAssignments";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

const directory = (name = "Alice", revision = 1): ExpertAssignmentOverview => ({
  source: { source_user_id: 1, revision },
  administrators: [],
  experts: [],
  employees: [
    {
      id: 2,
      username: name,
      display_name: null,
      disabled: false,
      communication_user_id: "native:2",
      mode: "assigned_only",
      revision: 1,
      expert_ids: [],
    },
  ],
});
const catalog = (revision = 1): ExpertResourceCatalog => ({
  source: { source_user_id: 1, revision },
  experts: [{ agent_id: "source", name: "Expert", description: null }],
  skills: [],
  defaults: [],
});
function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { resolve, promise };
}
beforeEach(() => {
  vi.spyOn(expertAssignmentsApi, "directory").mockResolvedValue(directory());
  vi.spyOn(expertAssignmentsApi, "catalog").mockResolvedValue(catalog());
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("shows assignments while the separate catalog is slow", async () => {
  const slow = pending<ExpertResourceCatalog>();
  vi.mocked(expertAssignmentsApi.catalog).mockReturnValue(slow.promise);
  const { result } = renderHook(() => useExpertAssignments(true, 1));
  await waitFor(() =>
    expect(result.current.data?.employees[0].username).toBe("Alice"),
  );
  expect(result.current.catalogReady).toBe(false);
  await act(async () => slow.resolve(catalog()));
  expect(result.current.catalogReady).toBe(true);
  expect(result.current.data?.experts[0].name).toBe("Expert");
});

it("retains assignments on partial failure and retries the catalog", async () => {
  vi.mocked(expertAssignmentsApi.catalog).mockRejectedValueOnce(
    new Error("catalog offline"),
  );
  const { result } = renderHook(() => useExpertAssignments(true, 1));
  await waitFor(() => expect(result.current.error).toBe("catalog offline"));
  expect(result.current.data?.employees[0].username).toBe("Alice");
  expect(result.current.catalogReady).toBe(false);
  await act(async () => result.current.reload());
  expect(result.current.catalogReady).toBe(true);
  vi.mocked(expertAssignmentsApi.directory).mockRejectedValueOnce(
    new Error("directory offline"),
  );
  await act(async () => {
    await result.current.reload().catch(() => {});
  });
  expect(result.current.data?.employees[0].username).toBe("Alice");
  expect(result.current.error).toBe("directory offline");
});

it("does not combine a resource catalog with a different source revision", async () => {
  vi.mocked(expertAssignmentsApi.catalog).mockResolvedValue(catalog(2));
  const { result } = renderHook(() => useExpertAssignments(true, 1));
  await waitFor(() =>
    expect(result.current.error).toBe(
      "expertAccess.errors.policy_revision_conflict",
    ),
  );
  expect(result.current.data?.experts).toEqual([]);
  expect(result.current.catalogReady).toBe(false);
});

it("cancels old reads and ignores late responses after account changes", async () => {
  const old = pending<ExpertAssignmentOverview>();
  const next = pending<ExpertAssignmentOverview>();
  vi.mocked(expertAssignmentsApi.directory)
    .mockReturnValueOnce(old.promise)
    .mockReturnValueOnce(next.promise);
  const { result, rerender } = renderHook(
    ({ id }) => useExpertAssignments(true, id),
    { initialProps: { id: 1 } },
  );
  const signal = vi.mocked(expertAssignmentsApi.directory).mock.calls[0][0]!;
  rerender({ id: 3 });
  expect(signal.aborted).toBe(true);
  expect(result.current.data).toBeUndefined();
  await act(async () => old.resolve(directory("old account")));
  expect(result.current.data).toBeUndefined();
  await act(async () => next.resolve(directory("new account")));
  expect(result.current.data?.employees[0].username).toBe("new account");
});

it("ignores superseded refreshes within the same account", async () => {
  const { result } = renderHook(() => useExpertAssignments(true, 1));
  await waitFor(() => expect(result.current.catalogReady).toBe(true));
  const old = pending<ExpertAssignmentOverview>();
  vi.mocked(expertAssignmentsApi.directory)
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(directory("latest"));
  let first!: Promise<void>;
  act(() => {
    first = result.current.reload();
  });
  const signal = vi
    .mocked(expertAssignmentsApi.directory)
    .mock.calls.at(-1)![0]!;
  await act(async () => result.current.reload());
  expect(signal.aborted).toBe(true);
  await act(async () => {
    old.resolve(directory("old"));
    await first;
  });
  expect(result.current.data?.employees[0].username).toBe("latest");
});
