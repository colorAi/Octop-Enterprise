import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  usersApi,
  type UserPage,
  type UserRow,
} from "../../../api/modules/users";
import { useUserDirectory } from "./useUserDirectory";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});

function page(username: string, total = 1): UserPage {
  return {
    items: [
      {
        id: 2,
        username,
        role: "user",
        display_name: null,
        disabled: false,
        agent_count: 3,
      } as UserRow,
    ],
    total,
    page: 1,
    page_size: 25,
  };
}

function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() =>
  vi.spyOn(usersApi, "page").mockResolvedValue(page("first", 60)),
);
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("loads counts without loading experts and preserves rows on refresh failure", async () => {
  const experts = vi.spyOn(usersApi, "agents");
  const { result } = renderHook(() => useUserDirectory(1, ""));
  await waitFor(() => expect(result.current.rows[0]?.username).toBe("first"));
  expect(result.current.rows[0].agent_count).toBe(3);
  expect(experts).not.toHaveBeenCalled();
  vi.mocked(usersApi.page).mockRejectedValueOnce(
    new Error("directory offline"),
  );
  await act(() => result.current.refresh());
  expect(result.current.rows[0].username).toBe("first");
  expect(result.current.error).toBe("directory offline");
  expect(result.current.loading).toBe(false);
});

it("paginates on the server and debounces search back to page one", async () => {
  const { result, rerender } = renderHook(
    ({ search }) => useUserDirectory(1, search),
    { initialProps: { search: "" } },
  );
  await waitFor(() => expect(result.current.loading).toBe(false));
  act(() => result.current.changePage(2, 25));
  await waitFor(() =>
    expect(usersApi.page).toHaveBeenLastCalledWith(
      2,
      25,
      "",
      expect.any(AbortSignal),
    ),
  );
  rerender({ search: "fin" });
  rerender({ search: "finance" });
  await waitFor(() =>
    expect(usersApi.page).toHaveBeenLastCalledWith(
      1,
      25,
      "finance",
      expect.any(AbortSignal),
    ),
  );
  expect(
    vi.mocked(usersApi.page).mock.calls.some((call) => call[2] === "fin"),
  ).toBe(false);
});

it("ignores superseded responses and clears data on account changes", async () => {
  const old = pending<UserPage>();
  const fresh = pending<UserPage>();
  const { result, rerender } = renderHook(
    ({ identity }) => useUserDirectory(identity, ""),
    { initialProps: { identity: 1 } },
  );
  await waitFor(() => expect(result.current.rows[0]?.username).toBe("first"));
  vi.mocked(usersApi.page)
    .mockReturnValueOnce(old.promise)
    .mockReturnValueOnce(fresh.promise);
  let refresh!: Promise<void>;
  act(() => {
    refresh = result.current.refresh();
  });
  const oldSignal = vi.mocked(usersApi.page).mock.calls.at(-1)![3];
  rerender({ identity: 2 });
  expect(result.current.rows).toEqual([]);
  expect(oldSignal.aborted).toBe(true);
  await act(async () => {
    old.resolve(page("old account"));
    await refresh;
  });
  expect(result.current.rows).toEqual([]);
  await act(async () => fresh.resolve(page("new account")));
  expect(result.current.rows[0].username).toBe("new account");
});

it("moves back from an emptied final page and supports empty search results", async () => {
  const { result } = renderHook(() => useUserDirectory(1, ""));
  await waitFor(() => expect(result.current.loading).toBe(false));
  vi.mocked(usersApi.page).mockResolvedValue({
    items: [],
    total: 0,
    page: 3,
    page_size: 25,
  });
  act(() => result.current.changePage(3, 25));
  await waitFor(() => expect(result.current.page).toBe(1));
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.rows).toEqual([]);
  expect(result.current.total).toBe(0);
});
