import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App } from "antd";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usersApi, type UserRow } from "../../../api/modules/users";
import type { OctopAgent } from "../../../context/AgentContext";
import UsersListPanel from "./UsersListPanel";
import { request } from "../../../api/request";

vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 1, role: "admin" }),
  useSetCurrentUser: () => () => undefined,
}));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../../api/request", () => ({ request: vi.fn(async () => []) }));
vi.mock("../../../api/modules/userRoles", () => ({
  userRolesApi: { list: vi.fn(async () => []) },
  snapshotRoleLabel: () => "user",
  userRoleDrift: () => false,
  userRoleLabel: () => "user",
}));
vi.mock("../../Experts/components/agentBackendForm", () => ({
  fetchFilesystemDefaults: async () => ({ tree_root: "/" }),
}));
vi.mock("../../Experts/components/EditAgentDrawer", () => ({
  default: () => null,
}));
vi.mock("../../Experts/components/RootDirSelect", () => ({
  default: () => null,
}));
vi.mock("./InviteDrawer", () => ({ default: () => null }));
vi.mock("../../Experts/components/AgentCard", () => ({
  AgentCard: ({ agent }: { agent: OctopAgent }) => <div>{agent.name}</div>,
}));

beforeEach(() => {
  const getComputedStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) =>
    getComputedStyle(element),
  );
  vi.spyOn(usersApi, "models").mockResolvedValue({
    model_refs: [],
    revision: "rev-1",
    items: [
      {
        ref: "alpha/chat",
        provider_name: "alpha",
        model: "chat",
        name: "Chat",
      },
    ],
  });
  vi.spyOn(usersApi, "page").mockResolvedValue({
    items: [
      {
        id: 2,
        username: "Alice",
        role: "user",
        display_name: null,
        disabled: false,
        agent_count: 2,
      },
      {
        id: 3,
        username: "Bob",
        role: "user",
        display_name: null,
        disabled: true,
        agent_count: 1,
      },
    ] as UserRow[],
    total: 2,
    page: 1,
    page_size: 25,
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it("saves knowledge grants with the employee's account settings", async () => {
  vi.spyOn(usersApi, "knowledgeBases").mockResolvedValue({
    knowledge_base_ids: [],
    items: [
      {
        id: "procedures",
        name: "Company procedures",
        description: "",
        shared: false,
      },
    ],
  });
  vi.mocked(request).mockClear();
  render(
    <App>
      <UsersListPanel />
    </App>,
  );
  const alice = await screen.findByText("Alice");
  fireEvent.click(
    within(alice.closest("tr")!).getByRole("button", { name: "common.edit" }),
  );
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "Company procedures" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "common.save", exact: true }),
  );
  await waitFor(() =>
    expect(
      vi
        .mocked(request)
        .mock.calls.some(
          ([url, options]) =>
            url === "/users/2" &&
            options?.method === "PATCH" &&
            JSON.parse(options.body as string).knowledge_base_ids?.[0] ===
              "procedures",
        ),
    ).toBe(true),
  );
  const patch = vi
    .mocked(request)
    .mock.calls.find(
      ([url, options]) => url === "/users/2" && options?.method === "PATCH",
    );
  expect(JSON.parse(patch![1]!.body as string)).not.toHaveProperty(
    "model_access",
  );
});

it("saves changed model grants and their revision with employee settings", async () => {
  vi.spyOn(usersApi, "knowledgeBases").mockResolvedValue({
    knowledge_base_ids: [],
    items: [],
  });
  vi.mocked(request).mockClear();
  render(
    <App>
      <UsersListPanel />
    </App>,
  );
  const alice = await screen.findByText("Alice");
  fireEvent.click(
    within(alice.closest("tr")!).getByRole("button", { name: "common.edit" }),
  );
  fireEvent.click(
    await screen.findByRole("checkbox", { name: "alpha / Chat" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "common.save", exact: true }),
  );
  await waitFor(() =>
    expect(
      vi
        .mocked(request)
        .mock.calls.some(
          ([url, options]) =>
            url === "/users/2" &&
            options?.method === "PATCH" &&
            JSON.parse(options.body as string).model_access?.model_refs?.[0] ===
              "alpha/chat",
        ),
    ).toBe(true),
  );
  const patch = vi
    .mocked(request)
    .mock.calls.find(
      ([url, options]) => url === "/users/2" && options?.method === "PATCH",
    );
  expect(JSON.parse(patch![1]!.body as string).model_access).toEqual({
    model_refs: ["alpha/chat"],
    expected_revision: "rev-1",
  });
});

it("shows users immediately and loads only the selected employee's experts", async () => {
  let resolveAlice!: (value: OctopAgent[]) => void;
  const agents = vi
    .spyOn(usersApi, "agents")
    .mockReturnValueOnce(
      new Promise((resolve) => {
        resolveAlice = resolve;
      }),
    )
    .mockResolvedValueOnce([
      { agent_id: "bob", name: "Bob expert" } as OctopAgent,
    ]);
  render(
    <App>
      <UsersListPanel />
    </App>,
  );
  await screen.findByText("Alice");
  expect(agents).not.toHaveBeenCalled();
  const aliceRow = screen.getByText("Alice").closest("tr")!;
  fireEvent.click(within(aliceRow).getByRole("button", { name: "2" }));
  await waitFor(() =>
    expect(agents).toHaveBeenCalledWith(2, expect.any(AbortSignal)),
  );
  const oldSignal = agents.mock.calls[0][1];
  const bobRow = screen.getByText("Bob").closest("tr")!;
  fireEvent.click(within(bobRow).getByRole("button", { name: "1" }));
  await screen.findByText("Bob expert");
  expect(oldSignal.aborted).toBe(true);
  await act(async () =>
    resolveAlice([
      { agent_id: "alice", name: "Stale Alice expert" } as OctopAgent,
    ]),
  );
  expect(screen.queryByText("Stale Alice expert")).toBeNull();
});

it("reports expert detail errors without removing the employee list", async () => {
  vi.spyOn(usersApi, "agents").mockRejectedValue(
    new Error("expert details offline"),
  );
  render(
    <App>
      <UsersListPanel />
    </App>,
  );
  const alice = await screen.findByText("Alice");
  fireEvent.click(
    within(alice.closest("tr")!).getByRole("button", { name: "2" }),
  );
  await screen.findByText("expert details offline");
  expect(screen.getByText("Bob")).toBeTruthy();
  expect(screen.queryByText("adminUsers.noAgents")).toBeNull();
});
