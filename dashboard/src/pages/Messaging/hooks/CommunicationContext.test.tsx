import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import { communicationApi } from "../../../api/modules/communication";
import {
  getAuthToken,
  setAuthToken,
  clearAuthToken,
} from "../../../api/request";
import {
  CommunicationProvider,
  useCommunication,
} from "./CommunicationContext";
import type { OctopUser } from "../../../api/modules/auth";
vi.mock("../../../api/communicationRealtime", () => ({
  connectCommunicationRealtime: vi.fn(() => vi.fn()),
}));
const token = (id: number, sid = "s", iat = 0) =>
  `header.${btoa(JSON.stringify({ sub: id, sid, iat }))}.sig`;
function Probe() {
  const { state } = useCommunication();
  return <div>{state.profile?.nickname ?? "empty"}</div>;
}
afterEach(() => {
  act(() => clearAuthToken());
  vi.restoreAllMocks();
});
it("boots under StrictMode, preserves state on renewal, and clears it on account changes and logout", async () => {
  setAuthToken(token(1));
  const snapshot = vi.spyOn(communicationApi, "snapshot").mockResolvedValue({
    snapshot_id: "s",
    until_seq: "9",
    cursor: "c",
    chunk_count: 1,
  });
  vi.spyOn(communicationApi, "chunk").mockImplementation(async () => ({
    snapshot_id: "s",
    chunk_index: 0,
    chunk_count: 1,
    events: [
      {
        seq: "9",
        type: "profile.updated",
        object_id: "",
        version: "0",
        payload: {
          user_id: getAuthToken().includes(
            btoa(JSON.stringify({ sub: 1, sid: "s", iat: 0 })),
          )
            ? "a"
            : "b",
          nickname:
            JSON.parse(atob(getAuthToken().split(".")[1])).sub === 1
              ? "Account A"
              : "Account B",
        },
      },
    ],
  }));
  vi.spyOn(communicationApi, "conversations").mockResolvedValue({
    items: [],
    has_more: false,
  });
  vi.spyOn(communicationApi, "sync").mockResolvedValue({
    events: [],
    cursor: "c",
    head_seq: "9",
    has_more: false,
  });
  const user = (id: number) =>
    ({
      id,
      username: `u${id}`,
      role: "user",
      display_name: null,
      locale: "zh",
      permissions: ["messaging"],
    }) as OctopUser;
  const setUser = vi.fn();
  const wrapper = (id: number) => (
    <StrictMode>
      <CurrentUserProvider user={user(id)} setUser={setUser}>
        <CommunicationProvider>
          <Probe />
        </CommunicationProvider>
      </CurrentUserProvider>
    </StrictMode>
  );
  const view = render(wrapper(1));
  await screen.findByText("Account A");
  expect(snapshot).toHaveBeenCalledTimes(1);
  act(() => setAuthToken(token(1, "s", 10)));
  expect(screen.getByText("Account A")).toBeInTheDocument();
  expect(snapshot).toHaveBeenCalledTimes(1);
  act(() => setAuthToken(token(2)));
  expect(screen.queryByText("Account A")).toBeNull();
  view.rerender(wrapper(2));
  await screen.findByText("Account B");
  act(() => clearAuthToken());
  await waitFor(() => expect(screen.queryByText("Account B")).toBeNull());
  expect(screen.getByText("empty")).toBeInTheDocument();
});
