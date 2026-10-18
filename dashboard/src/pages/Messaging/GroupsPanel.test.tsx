import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { communicationApi } from "../../api/modules/communication";
import { emptyMessagingState } from "./hooks/communicationClient";
import GroupsPanel from "./GroupsPanel";
import GroupDetails from "./GroupDetails";

const mock = vi.hoisted(() => ({
  t: (key: string) => key,
  user: { id: 1, role: "user", permissions: ["messaging", "contacts"] },
  state: {} as ReturnType<typeof emptyMessagingState>,
  client: {
    synchronize: vi.fn(() => Promise.resolve()),
    mutate: vi.fn((action: () => Promise<unknown>) => action()),
  },
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: mock.t }) }));
vi.mock("./hooks/CommunicationContext", () => ({
  useCommunication: () => mock,
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => mock.user,
}));
beforeEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  mock.user = { id: 1, role: "user", permissions: ["messaging", "contacts"] };
  mock.state = {
    ...emptyMessagingState(),
    phase: "ready",
    profile: { user_id: "self", nickname: "Me" },
    contacts: {
      peer: { user_id: "peer", nickname: "Colleague", can_message: true },
    },
  };
});
it("keeps project creation opt-in and exposes it after granting the capability", () => {
  const view = render(
    <MemoryRouter>
      <GroupsPanel />
    </MemoryRouter>,
  );
  expect(
    screen.queryByRole("button", { name: "communication.createProjectGroup" }),
  ).toBeNull();
  mock.user.permissions.push("groups_create");
  view.rerender(
    <MemoryRouter>
      <GroupsPanel />
    </MemoryRouter>,
  );
  expect(
    screen.getByRole("button", { name: "communication.createProjectGroup" }),
  ).toBeInTheDocument();
  expect(screen.queryByText("communication.manageGroups")).toBeNull();
});
it("reuses the same create request ID after a failed response", async () => {
  mock.user.permissions.push("groups_create");
  const create = vi
    .spyOn(communicationApi, "createProjectGroup")
    .mockRejectedValueOnce(new Error("connection lost"))
    .mockResolvedValueOnce({ conversation_id: "new-project" });
  render(
    <MemoryRouter>
      <GroupsPanel />
    </MemoryRouter>,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "communication.createProjectGroup" }),
  );
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("communication.groupName"), {
    target: { value: "Review project" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "OK" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(within(dialog).getByRole("button", { name: "OK" })).not.toHaveClass(
      "ant-btn-loading",
    ),
  );
  fireEvent.click(within(dialog).getByRole("button", { name: "OK" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));
  expect(create.mock.calls[0][0]).toEqual(create.mock.calls[1][0]);
  expect(create.mock.calls[0][0]).toMatchObject({
    name: "Review project",
    user_ids: [],
  });
});
it("shows a group type filter and keeps company group discoverable when hidden", () => {
  mock.state.conversations.company = {
    conversation_id: "company",
    type: "group",
    pinned: false,
    muted: false,
    last_seq: "1",
    hidden_at_seq: "1",
    group: {
      name: "All staff",
      group_kind: "company",
      announcement: null,
      owner_user_id: "peer",
      mute_all: false,
    },
  };
  render(
    <MemoryRouter>
      <GroupsPanel />
    </MemoryRouter>,
  );
  expect(screen.getByText("All staff")).toBeInTheDocument();
  expect(
    screen.getByRole("combobox", { name: "communication.groupKindFilter" }),
  ).toBeInTheDocument();
  expect(
    screen.getByText("communication.groupKinds.company"),
  ).toBeInTheDocument();
});
it("uses management metadata and requires explicit membership before opening history", async () => {
  const memberRead = vi.spyOn(communicationApi, "members");
  const groupRead = vi.spyOn(communicationApi, "group");
  vi.spyOn(communicationApi.organization, "group").mockResolvedValue({
    conversation_id: "dept",
    name: "Design",
    group_kind: "department",
    announcement: null,
    owner_user_id: "peer",
    mute_all: false,
    is_member: false,
    members: [
      {
        user_id: "peer",
        nickname: "Colleague",
        role: "owner",
        membership_source: "automatic",
      },
    ],
  });
  vi.spyOn(communicationApi.organization, "users").mockResolvedValue({
    items: [],
  });
  const join = vi.spyOn(communicationApi, "groupMembers").mockResolvedValue({});
  render(
    <MemoryRouter>
      <GroupDetails
        managed
        open
        close={() => undefined}
        conversation={{ conversation_id: "dept" }}
      />
    </MemoryRouter>,
  );
  expect(
    await screen.findByText("communication.groupManageOnly"),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "communication.openGroupChat" }),
  ).toBeNull();
  expect(
    screen.queryByRole("button", { name: "communication.removeMember" }),
  ).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "communication.joinGroup" }),
  );
  await waitFor(() =>
    expect(join).toHaveBeenCalledWith(
      "dept",
      { request_id: expect.any(String), user_ids: ["self"] },
      false,
      true,
    ),
  );
  expect(memberRead).not.toHaveBeenCalled();
  expect(groupRead).not.toHaveBeenCalled();
});
it("requires the owner to transfer ownership before leaving", async () => {
  vi.spyOn(communicationApi, "group").mockResolvedValue({
    name: "Project",
    group_kind: "project",
    my_role: "owner",
    announcement: null,
    owner_user_id: "self",
    mute_all: false,
    is_member: true,
  });
  vi.spyOn(communicationApi, "members").mockResolvedValue({
    items: [
      {
        user_id: "self",
        nickname: "Me",
        role: "owner",
        membership_source: "invited",
      },
    ],
  });
  render(
    <MemoryRouter>
      <GroupDetails
        open
        close={() => undefined}
        conversation={{ conversation_id: "project" }}
      />
    </MemoryRouter>,
  );
  expect(
    await screen.findByText("communication.ownerLeaveHint"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "communication.leaveGroup" }),
  ).toBeDisabled();
});

it.each(["member", "admin", "owner"] as const)(
  "keeps names visible and restricts management details for group role %s",
  async (role) => {
    const identity = {
      user_id: "peer",
      nickname: "Colleague",
      avatar_url: "https://example.test/colleague.png",
      job_title: "产品经理",
    };
    if (role === "member") delete mock.state.contacts.peer;
    else mock.state.contacts.peer = identity;
    vi.spyOn(communicationApi, "group").mockResolvedValue({
      name: "Company",
      group_kind: "company",
      my_role: role,
      announcement: "Welcome",
      owner_user_id: "owner",
      mute_all: false,
      is_member: true,
    });
    vi.spyOn(communicationApi, "members").mockResolvedValue({
      items: [
        {
          ...identity,
          role: "member",
          membership_source: "automatic",
        },
      ],
    });
    render(
      <MemoryRouter>
        <GroupDetails
          open
          close={() => undefined}
          conversation={{ conversation_id: "company" }}
        />
      </MemoryRouter>,
    );
    expect(await screen.findByText("Colleague")).toBeInTheDocument();
    expect(screen.getByText("产品经理")).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: "Colleague" }).querySelector("img"),
    ).toHaveAttribute("src", identity.avatar_url);
    expect(screen.getByText("Welcome")).toBeInTheDocument();
    expect(screen.queryByText("communication.compulsoryGroup")).toBeNull();
    if (role === "member") {
      expect(
        screen.queryByText("communication.memberSources.automatic"),
      ).toBeNull();
      expect(
        screen.queryByText("communication.groupManagementSettings"),
      ).toBeNull();
      expect(
        screen.queryByRole("button", { name: "communication.manageMember" }),
      ).toBeNull();
    } else {
      expect(
        screen.getByText("communication.memberSources.automatic"),
      ).toBeInTheDocument();
      fireEvent.click(
        screen.getByText("communication.groupManagementSettings"),
      );
      expect(
        await screen.findByText("communication.compulsoryGroup"),
      ).toBeVisible();
      expect(screen.queryByText("communication.muteMemberHour")).toBeNull();
      fireEvent.click(
        screen.getByRole("button", { name: "communication.manageMember" }),
      );
      await waitFor(() =>
        expect(screen.getByText("communication.muteMemberHour")).toBeVisible(),
      );
    }
  },
);

it("keeps a successfully created group recoverable when list synchronization fails", async () => {
  mock.user.permissions.push("groups_create");
  const create = vi
    .spyOn(communicationApi, "createProjectGroup")
    .mockResolvedValue({ conversation_id: "created" });
  mock.client.synchronize
    .mockRejectedValueOnce(new Error("sync failed"))
    .mockResolvedValue(undefined);
  render(
    <MemoryRouter>
      <GroupsPanel />
    </MemoryRouter>,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "communication.createProjectGroup" }),
  );
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getByLabelText("communication.groupName"), {
    target: { value: "Created once" },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "OK" }));
  expect(
    await screen.findByText("communication.groupRefreshFailed"),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "communication.openGroupChat" }),
  );
  await waitFor(() => expect(mock.client.synchronize).toHaveBeenCalledTimes(2));
  expect(create).toHaveBeenCalledTimes(1);
});
