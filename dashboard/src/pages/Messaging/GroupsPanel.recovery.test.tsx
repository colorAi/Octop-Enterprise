import { useSyncExternalStore } from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { communicationApi } from "../../api/modules/communication";
import type { Conversation } from "../../api/types/communication";
import { CommunicationClient } from "./hooks/communicationClient";
import GroupsPanel from "./GroupsPanel";

const fixture = vi.hoisted(() => ({
  token: "",
  client: null as CommunicationClient | null,
  t: (key: string) => key,
}));
vi.mock("../../api/request", () => ({
  getAuthToken: () => fixture.token,
  request: vi.fn(),
  requestBlob: vi.fn(),
}));
vi.mock("../../api/communicationRealtime", () => ({
  connectCommunicationRealtime: vi.fn(() => vi.fn()),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: fixture.t }) }));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({
    id: 1,
    role: "user",
    permissions: ["contacts", "messaging", "groups_create", "departments"],
  }),
}));
vi.mock("./hooks/CommunicationContext", () => ({
  useCommunication: () => {
    const client = fixture.client!;
    const state = useSyncExternalStore(client.subscribe, client.getSnapshot);
    return { client, state };
  },
}));

function Location() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

beforeEach(async () => {
  fixture.token = `header.${btoa(
    JSON.stringify({ sub: 1, sid: "session" }),
  )}.signature`;
  vi.spyOn(communicationApi, "snapshot").mockResolvedValue({
    snapshot_id: "s",
    chunk_count: 1,
    until_seq: "9",
    cursor: "cursor9",
  });
  vi.spyOn(communicationApi, "chunk").mockResolvedValue({
    snapshot_id: "s",
    chunk_index: 0,
    chunk_count: 1,
    events: [
      {
        type: "profile.updated",
        seq: "9",
        version: "0",
        object_id: "self",
        payload: { user_id: "self", nickname: "Me" },
      },
    ],
  });
  vi.spyOn(communicationApi, "conversations").mockResolvedValue({
    items: [],
    has_more: false,
  });
  vi.spyOn(communicationApi, "contacts").mockResolvedValue({ items: [] });
  vi.spyOn(communicationApi, "departments").mockResolvedValue({ items: [] });
  vi.spyOn(communicationApi, "directorySettings").mockResolvedValue({
    name: "Company",
  });
  vi.spyOn(communicationApi, "sync").mockResolvedValue({
    events: [],
    cursor: "cursor9",
    head_seq: "9",
    has_more: false,
  });
  fixture.client = new CommunicationClient("1:session", true, true);
  await fixture.client.synchronize();
});
afterEach(() => {
  fixture.client?.stop();
  vi.restoreAllMocks();
});

it.each(["project", "company"] as const)(
  "recovers an already-created %s group using the real synchronization client",
  async (kind) => {
    const create = vi
      .spyOn(
        communicationApi,
        kind === "project" ? "createProjectGroup" : "createCompanyGroup",
      )
      .mockResolvedValue({ conversation_id: "created" });
    vi.mocked(communicationApi.sync).mockRejectedValue(
      new Error("network lost after create"),
    );
    render(
      <MemoryRouter initialEntries={["/contacts"]}>
        <GroupsPanel />
        <Location />
      </MemoryRouter>,
    );
    if (kind === "project") {
      fireEvent.click(
        screen.getByRole("button", {
          name: "communication.createProjectGroup",
        }),
      );
      const dialog = await screen.findByRole("dialog");
      fireEvent.change(
        within(dialog).getByLabelText("communication.groupName"),
        { target: { value: "Created once" } },
      );
      fireEvent.click(within(dialog).getByRole("button", { name: "OK" }));
    } else {
      fireEvent.click(
        screen.getByRole("button", {
          name: "communication.enableCompanyGroup",
        }),
      );
    }
    expect(
      await screen.findByText("communication.groupRefreshFailed"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/contacts");
    const reopen = screen.getByRole("button", {
      name: "communication.openGroupChat",
    });
    const calls = vi.mocked(communicationApi.sync).mock.calls.length;
    fireEvent.click(reopen);
    await waitFor(() =>
      expect(communicationApi.sync).toHaveBeenCalledTimes(calls + 1),
    );
    await waitFor(() => expect(reopen).not.toHaveClass("ant-btn-loading"));
    expect(screen.getByTestId("location")).toHaveTextContent("/contacts");
    expect(
      screen.getByRole("button", { name: "communication.openGroupChat" }),
    ).toBeInTheDocument();

    const group: Conversation = {
      conversation_id: "created",
      type: "group",
      last_seq: "1",
      pinned: false,
      muted: false,
      group: {
        name: "Created once",
        group_kind: kind,
        announcement: null,
        owner_user_id: "self",
        mute_all: false,
      },
    };
    vi.mocked(communicationApi.sync).mockResolvedValue({
      events: [
        {
          type: "group.members.changed",
          seq: "10",
          version: "1",
          object_id: "created",
          payload: { conversation_id: "created" },
        },
      ],
      cursor: "cursor10",
      head_seq: "10",
      has_more: false,
    });
    vi.mocked(communicationApi.snapshot).mockResolvedValue({
      snapshot_id: "s",
      chunk_count: 1,
      until_seq: "10",
      cursor: "cursor10",
    });
    vi.mocked(communicationApi.chunk).mockResolvedValue({
      snapshot_id: "s",
      chunk_index: 0,
      chunk_count: 1,
      events: [
        {
          type: "profile.updated",
          seq: "10",
          version: "0",
          object_id: "self",
          payload: { user_id: "self", nickname: "Me" },
        },
        {
          type: "conversation.created",
          seq: "10",
          version: "1",
          object_id: "created",
          payload: { ...group },
        },
      ],
    });
    vi.mocked(communicationApi.conversations).mockResolvedValue({
      items: [group],
      has_more: false,
    });
    fireEvent.click(
      screen.getByRole("button", { name: "communication.openGroupChat" }),
    );
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent(
        "/messages/created",
      ),
    );
    expect(fixture.client!.state.conversations.created).toBeDefined();
    expect(create).toHaveBeenCalledTimes(1);
  },
);
