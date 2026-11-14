import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MessagingPage from "../Contacts";
import { communicationApi } from "../../api/modules/communication";
import { emptyMessagingState } from "./hooks/communicationClient";
import { ConversationPageVisibility } from "../../hooks/useConversationPageVisible";
import type { GroupMember } from "../../api/types/communication";
const mock = vi.hoisted(() => ({
  state: {} as unknown,
  client: {
    scrollPositions: new Map<string, number>(),
    focus: vi.fn(),
    read: vi.fn(() => Promise.resolve()),
    send: vi.fn(() => Promise.resolve()),
    synchronize: vi.fn(),
    image: vi.fn(),
    file: vi.fn(),
    mutate: vi.fn((action: () => Promise<unknown>) => action()),
  },
}));
vi.mock("./hooks/CommunicationContext", () => ({
  useCommunication: () => mock,
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({
    id: 1,
    role: "user",
    permissions: ["messaging", "contacts"],
  }),
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "Asia/Shanghai",
}));
vi.mock("../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../hooks/useAuthImageSrc", () => ({
  useAuthImageSrc: (url: string) => ({ src: url, loadState: "ready" }),
}));
afterEach(() => vi.restoreAllMocks());
beforeEach(() => {
  vi.clearAllMocks();
  mock.state = {
    ...emptyMessagingState(),
    phase: "ready",
    profile: { user_id: "a", nickname: "A" },
    contacts: { b: { user_id: "b", nickname: "Bob" } },
    conversations: {
      c: {
        conversation_id: "c",
        type: "private",
        peer_user_id: "b",
        last_seq: "1",
        pinned: false,
        muted: false,
        can_send: true,
      },
    },
    histories: {
      c: [
        {
          message_id: "m",
          seq: "1",
          type: "text",
          sender_id: "b",
          text: "<script>danger()</script>",
          created_at: "2026-10-12T10:00:00Z",
          recalled: false,
        },
      ],
    },
  };
});
it("renders messages as text and sends through the independent communication client", async () => {
  const view = render(
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText("<script>danger()</script>")).toBeInTheDocument();
  expect(view.container.querySelector("script")).toBeNull();
  const input = screen.getByPlaceholderText("communication.compose");
  fireEvent.change(input, { target: { value: "Hello colleague" } });
  fireEvent.keyDown(input, { key: "Enter", code: "Enter" });
  expect(mock.client.send).toHaveBeenCalledWith(
    "c",
    expect.objectContaining({
      text: "Hello colleague",
      client_message_id: expect.stringMatching(/^[a-f0-9]{32}$/),
    }),
  );
  expect(mock.client.focus).toHaveBeenCalledWith("c");
});

function openMentionGroup(
  members: GroupMember[] = [
    { user_id: "a", nickname: "Alice" },
    { user_id: "b", nickname: "李明", job_title: "设计" },
    { user_id: "c", nickname: "李明", job_title: "研发" },
    { user_id: "disabled", nickname: "Former colleague", archived: true },
  ],
) {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.conversations.c = {
    ...state.conversations.c,
    type: "group",
    membership_epochs: [
      {
        epoch: 1,
        role: "member",
        visible_from_seq: "0",
        visible_through_seq: null,
        left_at: null,
        mute_until: null,
      },
    ],
    group: {
      name: "Project",
      announcement: null,
      owner_user_id: "a",
      mute_all: false,
    },
  };
  const roster = vi
    .spyOn(communicationApi, "members")
    .mockResolvedValue({ items: members });
  const view = render(
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>,
  );
  const input = screen.getByPlaceholderText(
    "communication.compose",
  ) as HTMLTextAreaElement;
  return { input, roster, ...view };
}

it("mentions only selected group members, distinguishes duplicate names and resets after sending", async () => {
  const { input, roster } = openMentionGroup();
  expect(roster).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "communication.mentionColleague" }),
  );
  const option = await screen.findByRole("option", { name: /李明 研发/ });
  expect(screen.getAllByRole("option")).toHaveLength(2);
  expect(roster).toHaveBeenCalledWith("c", expect.any(AbortSignal));
  fireEvent.click(option);
  expect(input.value).toBe("@李明 ");
  fireEvent.change(input, { target: { value: "@李明 请确认" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).toHaveBeenLastCalledWith(
    "c",
    expect.objectContaining({ text: "@李明 请确认", mention_user_ids: ["c"] }),
  );
  expect(input.value).toBe("");
  fireEvent.change(input, { target: { value: "普通消息" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).toHaveBeenLastCalledWith(
    "c",
    expect.not.objectContaining({ mention_user_ids: expect.anything() }),
  );
});

it("selects exactly one authorized expert separately from colleague mentions and resets it after send", async () => {
  const first = {
    binding_id: "expert-one",
    source_agent_id: "source",
    source_row_id: 10,
    generation: 1,
    state: "active" as const,
    display_name: "Planner",
    avatar: {},
    can_call: true,
  };
  vi.spyOn(communicationApi, "groupExperts").mockResolvedValue({
    items: [
      first,
      {
        ...first,
        binding_id: "removed",
        display_name: "Removed expert",
        state: "removed",
      },
      {
        ...first,
        binding_id: "unassigned",
        display_name: "Unassigned expert",
        can_call: false,
      },
    ],
    candidates: [],
    experts_enabled: true,
    ready: true,
    can_manage: false,
    can_configure: false,
    syncing: false,
    scope_revision: 1,
    scope_sources: [],
  });
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.histories.c[0] = {
    ...state.histories.c[0],
    mention_expert_id: first.binding_id,
    expert_call: {
      call_id: "call",
      call_state: "pending_admission",
      expert_name: "Planner",
    },
  };
  const { input } = openMentionGroup();
  expect(screen.getByText("@Planner")).toBeInTheDocument();
  fireEvent.change(input, { target: { value: "@" } });
  fireEvent.click(await screen.findByRole("option", { name: /Planner/ }));
  expect(screen.getAllByText("@Planner")).toHaveLength(2);
  expect(
    screen.queryByText("groupExperts.boundary", { exact: false }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: /Removed expert|Unassigned expert/ }),
  ).not.toBeInTheDocument();
  expect(input.value).toBe("");
  fireEvent.change(input, { target: { value: "Please review" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).toHaveBeenLastCalledWith(
    "c",
    expect.objectContaining({
      text: "Please review",
      mention_expert_id: "expert-one",
    }),
  );
  expect(mock.client.send).toHaveBeenLastCalledWith(
    "c",
    expect.not.objectContaining({ mention_user_ids: expect.anything() }),
  );
  fireEvent.change(input, { target: { value: "Ordinary message" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).toHaveBeenLastCalledWith(
    "c",
    expect.not.objectContaining({ mention_expert_id: expect.anything() }),
  );
});

it("mentions a sender from the avatar context menu at the current caret using their current group nickname", async () => {
  const { input, roster } = openMentionGroup([
    { user_id: "b", nickname: "Bob", my_nickname: "小李" },
  ]);
  fireEvent.change(input, {
    target: { value: "请 保留后文", selectionStart: 2, selectionEnd: 2 },
  });
  const avatar = screen.getByLabelText("Bob", { selector: "div" });
  fireEvent.click(avatar);
  expect(screen.queryByRole("menuitem")).not.toBeInTheDocument();
  expect(roster).not.toHaveBeenCalled();
  fireEvent.contextMenu(avatar);
  fireEvent.click(
    await screen.findByRole("menuitem", {
      name: "communication.mentionMember",
    }),
  );
  await waitFor(() => expect(input.value).toBe("请 @小李 保留后文"));
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).toHaveBeenCalledWith(
    "c",
    expect.objectContaining({
      text: "请 @小李 保留后文",
      mention_user_ids: ["b"],
    }),
  );
});

it("keeps the draft intact when a sender has left the group", async () => {
  const { input } = openMentionGroup([]);
  fireEvent.change(input, { target: { value: "还没写完" } });
  fireEvent.contextMenu(screen.getByLabelText("Bob", { selector: "div" }));
  fireEvent.click(
    await screen.findByRole("menuitem", {
      name: "communication.mentionMember",
    }),
  );
  await screen.findByText("communication.mentionMemberUnavailable");
  expect(input.value).toBe("还没写完");
  expect(mock.client.send).not.toHaveBeenCalled();
});

it("filters mentions and uses Enter to select without sending or interfering with IME", async () => {
  const { input } = openMentionGroup();
  fireEvent.change(input, { target: { value: "请@研发", selectionStart: 4 } });
  await screen.findByRole("option", { name: /李明 研发/ });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(input.value).toBe("请@研发");
  expect(mock.client.send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(input.value).toBe("请@李明 ");
  expect(mock.client.send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
  expect(mock.client.send).not.toHaveBeenCalled();
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).toHaveBeenCalledWith(
    "c",
    expect.objectContaining({ mention_user_ids: ["c"] }),
  );
});

it("keeps the keyboard selection when the browser reports an unchanged caret", async () => {
  const user = userEvent.setup();
  const { input } = openMentionGroup();
  await user.click(input);
  await user.type(input, "@");
  await screen.findByRole("option", { name: /李明 研发/ });
  await user.keyboard("{ArrowDown}{Enter}");
  expect(mock.client.send).not.toHaveBeenCalled();
  await user.keyboard("{Enter}");
  expect(mock.client.send).toHaveBeenCalledWith(
    "c",
    expect.objectContaining({ mention_user_ids: ["c"] }),
  );
});

it("removes the selected recipient after editing their mention and preserves the text after the caret", async () => {
  const { input } = openMentionGroup();
  fireEvent.change(input, {
    target: { value: "请 @研 确认", selectionStart: 4 },
  });
  fireEvent.click(await screen.findByRole("option", { name: /李明 研发/ }));
  expect(input.value).toBe("请 @李明  确认");
  fireEvent.change(input, { target: { value: "请 @李华 确认" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).toHaveBeenLastCalledWith(
    "c",
    expect.not.objectContaining({ mention_user_ids: expect.anything() }),
  );
});

it("lets member loading fail and retry without losing the draft or sending it", async () => {
  const { input, roster } = openMentionGroup();
  roster.mockRejectedValueOnce(new Error("offline"));
  fireEvent.change(input, { target: { value: "@" } });
  await screen.findByRole("alert");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(mock.client.send).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "communication.retry" }));
  await screen.findByRole("option", { name: /李明 研发/ });
  expect(input.value).toBe("@");
  fireEvent.keyDown(input, { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

it("cancels an outdated member request when the mention menu closes", async () => {
  const { input, roster } = openMentionGroup();
  let complete: ((result: { items: GroupMember[] }) => void) | undefined;
  roster.mockReturnValueOnce(
    new Promise((resolve) => {
      complete = resolve;
    }),
  );
  fireEvent.change(input, { target: { value: "@" } });
  await waitFor(() => expect(roster).toHaveBeenCalled());
  const signal = roster.mock.calls[0][1];
  fireEvent.keyDown(input, { key: "Escape" });
  expect(signal?.aborted).toBe(true);
  complete?.({ items: [{ user_id: "stale", nickname: "Stale member" }] });
  fireEvent.click(
    screen.getByRole("button", { name: "communication.mentionColleague" }),
  );
  await screen.findByRole("option", { name: /李明 研发/ });
  expect(
    screen.queryByRole("option", { name: /Stale member/ }),
  ).not.toBeInTheDocument();
});

it("does not offer mentions or load members when the group cannot send", () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.conversations.c.archived = true;
  const { input, roster } = openMentionGroup();
  expect(input).toBeDisabled();
  const button = screen.getByRole("button", {
    name: "communication.mentionColleague",
  });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(roster).not.toHaveBeenCalled();
});

it("marks a received mention for the current user but not for someone else or recalled messages", () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.histories.c = [
    { ...state.histories.c[0], mention_user_ids: ["a"] },
    {
      ...state.histories.c[0],
      message_id: "other",
      seq: "2",
      mention_user_ids: ["c"],
    },
    {
      ...state.histories.c[0],
      message_id: "recalled",
      seq: "3",
      mention_user_ids: ["a"],
      recalled: true,
    },
  ];
  openMentionGroup();
  expect(screen.getAllByText("communication.mentionedYou")).toHaveLength(1);
});

it("uses account portraits and job titles in recent chats, the header and message senders, including updates", () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.contacts.b = {
    ...state.contacts.b,
    avatar_url: "/api/users/2/avatar?v=1",
    avatar_icon: "business",
    job_title: "产品经理",
  };
  const page = () => (
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>
  );
  const view = render(page());
  expect(screen.getAllByText("产品经理")).toHaveLength(3);
  expect(
    view.container.querySelectorAll('img[src="/api/users/2/avatar?v=1"]'),
  ).toHaveLength(3);
  state.contacts.b = {
    ...state.contacts.b,
    avatar_url: null,
    job_title: "研发负责人",
  };
  view.rerender(page());
  expect(screen.queryByText("产品经理")).not.toBeInTheDocument();
  expect(screen.getAllByText("研发负责人")).toHaveLength(3);
  expect(
    view.container.querySelector('img[src="/api/users/2/avatar?v=1"]'),
  ).toBeNull();
  expect(view.container.querySelectorAll(".lucide-briefcase")).toHaveLength(3);
  fireEvent.change(
    screen.getByPlaceholderText("communication.searchConversations"),
    { target: { value: "研发负责人" } },
  );
  expect(screen.getAllByText("Bob")).toHaveLength(3);
});

it("uses the current account portrait for own messages without lending it to unknown senders", () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.profile = {
    user_id: "a",
    nickname: "Alice",
    avatar_url: "/api/users/1/avatar?v=1",
  };
  state.histories.c = [
    { ...state.histories.c[0], sender_id: "a", message_id: "own" },
    {
      ...state.histories.c[0],
      sender_id: "unknown",
      message_id: "unknown",
      seq: "2",
    },
  ];
  const view = render(
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(
    view.container.querySelectorAll('img[src="/api/users/1/avatar?v=1"]'),
  ).toHaveLength(1);
  expect(screen.getByText("communication.colleague")).toBeInTheDocument();
});

it("lists joined groups even when hidden from recent conversations, without listing private or archived chats", async () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.conversations.hiddenGroup = {
    conversation_id: "hiddenGroup",
    type: "group",
    group: {
      name: "Hidden department",
      announcement: null,
      owner_user_id: "a",
      mute_all: false,
    },
    last_seq: "6",
    hidden_at_seq: "6",
    pinned: false,
    muted: false,
    can_send: true,
  };
  state.conversations.formerGroup = {
    ...state.conversations.hiddenGroup,
    conversation_id: "formerGroup",
    archived: true,
    group: {
      ...state.conversations.hiddenGroup.group!,
      name: "Former department",
    },
  };
  state.histories.hiddenGroup = [
    {
      message_id: "group-message",
      seq: "6",
      type: "text",
      sender_id: "b",
      text: "Authorized group history",
      created_at: "2026-10-12T10:00:00Z",
      recalled: false,
    },
  ];
  render(
    <MemoryRouter initialEntries={["/messages"]}>
      <Routes>
        <Route path="/messages/:conversationId?" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.queryByText("Hidden department")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("communication.groupChats"));
  expect(screen.getByText("Hidden department")).toBeInTheDocument();
  expect(screen.queryByText("Former department")).not.toBeInTheDocument();
  expect(
    within(screen.getByRole("complementary")).queryByText("B"),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Hidden department"));
  expect(
    await screen.findByText("Authorized group history"),
  ).toBeInTheDocument();
  expect(mock.client.focus).toHaveBeenLastCalledWith("hiddenGroup");
});

it("preserves the draft while hidden without focusing or reading new company messages", () => {
  function Workspace({ visible }: { visible: boolean }) {
    return (
      <MemoryRouter initialEntries={["/messages/c"]}>
        <ConversationPageVisibility.Provider value={visible}>
          <Routes>
            <Route
              path="/messages/:conversationId"
              element={<MessagingPage />}
            />
          </Routes>
        </ConversationPageVisibility.Provider>
      </MemoryRouter>
    );
  }
  const view = render(<Workspace visible />);
  const input = screen.getByPlaceholderText("communication.compose");
  fireEvent.change(input, { target: { value: "Still writing" } });
  view.rerender(<Workspace visible={false} />);
  expect(mock.client.focus).toHaveBeenLastCalledWith(null);
  mock.client.focus.mockClear();
  mock.client.read.mockClear();
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  mock.state = {
    ...state,
    histories: {
      c: [
        ...state.histories.c,
        {
          ...state.histories.c[0],
          message_id: "new",
          seq: "2",
          text: "New while hidden",
        },
      ],
    },
  };
  view.rerender(<Workspace visible={false} />);
  fireEvent(document, new Event("visibilitychange"));
  expect(mock.client.focus).not.toHaveBeenCalled();
  expect(mock.client.read).not.toHaveBeenCalled();
  view.rerender(<Workspace visible />);
  expect(screen.getByPlaceholderText("communication.compose")).toBe(input);
  expect(input).toHaveValue("Still writing");
  expect(mock.client.focus).toHaveBeenLastCalledWith("c");
  expect(mock.client.read).toHaveBeenLastCalledWith("c", "2");
});

it("shows reply and forward directly under a message and recall only under the sender's message", async () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.histories.c.push({
    ...state.histories.c[0],
    message_id: "own",
    sender_id: "a",
    text: "My message",
    seq: "2",
  });
  render(
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(
    screen.getAllByRole("button", { name: "communication.reply" }),
  ).toHaveLength(2);
  expect(
    screen.getAllByRole("button", { name: "communication.forward" }),
  ).toHaveLength(2);
  expect(
    screen.getAllByRole("button", { name: "communication.recall" }),
  ).toHaveLength(1);
  expect(
    screen.queryByRole("button", { name: "communication.messageActions" }),
  ).not.toBeInTheDocument();
  fireEvent.click(
    screen.getAllByRole("button", { name: "communication.reply" })[0],
  );
  fireEvent.change(screen.getByPlaceholderText("communication.compose"), {
    target: { value: "Reply" },
  });
  fireEvent.click(screen.getByRole("button", { name: "communication.send" }));
  expect(mock.client.send).toHaveBeenCalledWith(
    "c",
    expect.objectContaining({ reply_to_message_id: "m", text: "Reply" }),
  );
  const recall = vi
    .spyOn(communicationApi, "recall")
    .mockResolvedValue(undefined);
  fireEvent.click(screen.getByRole("button", { name: "communication.recall" }));
  await waitFor(() => expect(recall).toHaveBeenCalledWith("own"));
  recall.mockRestore();
});
it("accepts a document as an attachment, shows its file card, and sends the ready asset", async () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  state.assets.document = {
    asset_id: "document",
    state: "ready",
    purpose: "chat_file",
    filename: "report.pdf",
    size_bytes: 7,
  };
  mock.client.file.mockResolvedValue("document");
  const view = render(
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>,
  );
  const file = new File(["report"], "report.pdf", { type: "application/pdf" });
  fireEvent.change(
    view.container.querySelector('input[type="file"]:not([accept])')!,
    { target: { files: [file] } },
  );
  await waitFor(() =>
    expect(screen.getByText("report.pdf")).toBeInTheDocument(),
  );
  expect(mock.client.file).toHaveBeenCalledWith(file);
  expect(mock.client.image).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "communication.send" }));
  expect(mock.client.send).toHaveBeenCalledWith(
    "c",
    expect.objectContaining({ asset_ids: ["document"] }),
  );
});

it("previews a quoted message outside the current page and opens its full original", async () => {
  const state = mock.state as ReturnType<typeof emptyMessagingState>;
  const original = {
    ...state.histories.c[0],
    message_id: "old",
    text: "Original outside the page",
  };
  state.histories.c[0] = {
    ...state.histories.c[0],
    text: "A reply",
    reply_to_message_id: "old",
    reply_preview: original,
  };
  const fetchOriginal = vi
    .spyOn(communicationApi, "message")
    .mockResolvedValue(original);
  render(
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText("Original outside the page")).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "communication.viewOriginal" }),
  );
  await waitFor(() =>
    expect(screen.getAllByText("Original outside the page")).toHaveLength(2),
  );
  expect(fetchOriginal).toHaveBeenCalledWith(
    "c",
    "old",
    expect.any(AbortSignal),
  );
  fetchOriginal.mockRestore();
});

it("has a separate delete action that removes only the current conversation", async () => {
  const remove = vi
    .spyOn(communicationApi, "deleteConversation")
    .mockResolvedValue(undefined);
  const clear = vi
    .spyOn(communicationApi, "clear")
    .mockResolvedValue(undefined);
  render(
    <MemoryRouter initialEntries={["/messages/c"]}>
      <Routes>
        <Route path="/messages/:conversationId" element={<MessagingPage />} />
        <Route path="/messages" element={<div>Conversation list</div>} />
      </Routes>
    </MemoryRouter>,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "communication.actions" }),
  );
  fireEvent.click(await screen.findByText("communication.deleteConversation"));
  expect(
    await screen.findByText("communication.deleteConversationHint"),
  ).toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "communication.deleteConversation" }),
  );
  await waitFor(() => expect(remove).toHaveBeenCalledWith("c"));
  expect(clear).not.toHaveBeenCalled();
  await screen.findByText("Conversation list");
  remove.mockRestore();
  clear.mockRestore();
});
