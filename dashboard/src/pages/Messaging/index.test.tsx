import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import MessagingPage from "../Contacts";
import { communicationApi } from "../../api/modules/communication";
import { emptyMessagingState } from "./hooks/communicationClient";
import { ConversationPageVisibility } from "../../hooks/useConversationPageVisible";
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
