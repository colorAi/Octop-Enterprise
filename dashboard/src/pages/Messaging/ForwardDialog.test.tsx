import { StrictMode } from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { MessagingState } from "./hooks/communicationClient";
import type { OctopAgent } from "../../context/AgentContext";
import type { ForwardTarget } from "./forwardDelivery";
import ForwardDialog from "./ForwardDialog";

const mock = vi.hoisted(() => ({
  state: {} as MessagingState,
  agents: [] as OctopAgent[],
  identity: "session",
  send: vi.fn(),
  start: vi.fn(),
  synchronize: vi.fn(),
  navigate: vi.fn(),
  setActiveAgent: vi.fn(),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => mock.navigate }));
vi.mock("../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: mock.agents,
    setActiveAgent: mock.setActiveAgent,
  }),
}));
vi.mock("./hooks/CommunicationContext", () => ({
  useCommunication: () => ({
    state: mock.state,
    client: { synchronize: mock.synchronize },
  }),
}));
vi.mock("./hooks/communicationClient", () => ({
  authSessionIdentity: () => mock.identity,
}));
vi.mock("./forwardDelivery", () => ({
  ForwardDelivery: class {
    constructor(...args: unknown[]) {
      mock.start(...args);
    }
    send = mock.send;
  },
}));
const source = {
  kind: "agent" as const,
  content: { text: "Work result", attachments: [], localArtifacts: [] },
};

beforeEach(() => {
  vi.clearAllMocks();
  mock.identity = "session";
  mock.agents = [
    { agent_id: "agent", name: "Analyst", state: "running", is_owner: true },
    { agent_id: "stopped", name: "Stopped", state: "stopped" },
  ] as OctopAgent[];
  mock.state = {
    phase: "ready",
    profile: { user_id: "self", nickname: "Me" },
    contacts: {
      self: { user_id: "self", nickname: "Me" },
      alice: { user_id: "alice", nickname: "Alice", can_message: true },
      bob: { user_id: "bob", nickname: "Bob" },
      disabled: {
        user_id: "disabled",
        nickname: "Disabled",
        status: "disabled",
      },
    },
    conversations: {
      hidden: {
        conversation_id: "hidden",
        type: "group",
        last_seq: "3",
        hidden_at_seq: "3",
        pinned: false,
        muted: false,
        group: { name: "Hidden group", mute_all: false },
        membership_epochs: [
          { role: "member", left_at: null, mute_until: null },
        ],
      },
      muted: {
        conversation_id: "muted",
        type: "group",
        last_seq: "3",
        pinned: false,
        muted: false,
        group: { name: "Muted group", mute_all: true },
        membership_epochs: [
          { role: "member", left_at: null, mute_until: null },
        ],
      },
    },
  } as unknown as MessagingState;
  mock.send.mockImplementation(async (target: ForwardTarget) => ({
    target,
    path:
      target.kind === "agent" ? "/chat/agent/thread" : `/messages/${target.id}`,
  }));
  mock.synchronize.mockResolvedValue(undefined);
});
afterEach(() => {
  delete window.__octopRuntime;
});

async function choose(label: string) {
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(
    await screen.findByText(label, {
      selector: ".ant-select-item-option-content",
    }),
  );
}

it("offers colleagues without existing chats, hidden joined groups and enabled agents", async () => {
  render(<ForwardDialog source={source} onClose={vi.fn()} />);
  await choose("Alice");
  await choose("Hidden group");
  await choose("Analyst");
  expect(screen.getByRole("combobox")).not.toHaveAttribute(
    "aria-expanded",
    "true",
  );
  expect(screen.queryByText("Stopped")).toBeNull();
  expect(screen.queryByText("Disabled")).toBeNull();
  expect(screen.queryByText("Muted group")).toBeNull();
  expect(screen.queryByText("Me")).toBeNull();
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "communication.send",
    }),
  );
  await waitFor(() => expect(mock.send).toHaveBeenCalledTimes(3));
  expect(mock.send.mock.calls.map(([target]) => target.kind)).toEqual([
    "contact",
    "conversation",
    "agent",
  ]);
});

it("retains failed targets for retry and never resends the successful recipient", async () => {
  let failed = false;
  mock.send.mockImplementation(async (target: ForwardTarget) => {
    if (target.id === "bob" && !failed) {
      failed = true;
      throw new Error("temporary failure");
    }
    return { target, path: `/messages/${target.id}` };
  });
  render(<ForwardDialog source={source} onClose={vi.fn()} />);
  await choose("Alice");
  await choose("Bob");
  const button = within(screen.getByRole("dialog")).getByRole("button", {
    name: "communication.send",
  });
  fireEvent.click(button);
  await screen.findByText("Bob：temporary failure");
  await waitFor(() => expect(button).not.toHaveClass("ant-btn-loading"));
  fireEvent.click(button);
  await waitFor(() => expect(mock.send).toHaveBeenCalledTimes(3));
  expect(mock.send.mock.calls.map(([target]) => target.id)).toEqual([
    "alice",
    "bob",
    "bob",
  ]);
});

it("opens the new agent conversation from its successful delivery result", async () => {
  const close = vi.fn();
  render(
    <StrictMode>
      <ForwardDialog source={source} onClose={close} />
    </StrictMode>,
  );
  await choose("Analyst");
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "communication.send",
    }),
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "communication.forwardOpen" }),
  );
  expect(mock.setActiveAgent).toHaveBeenCalledWith("agent");
  expect(mock.navigate).toHaveBeenCalledWith("/chat/agent/thread");
  expect(close).toHaveBeenCalled();
});

it("closes the dialog when the account changes", () => {
  const close = vi.fn();
  render(<ForwardDialog source={source} onClose={close} />);
  mock.identity = "other-session";
  window.dispatchEvent(new Event("octop:auth-token-changed"));
  expect(close).toHaveBeenCalledTimes(1);
  expect(mock.send).not.toHaveBeenCalled();
});

it("selects one image without the answer text and sends the instructions in the same draft", async () => {
  const images = ["first.png", "second.png"].map((filename) => ({
    filename,
    url: `https://example.com/${filename}`,
    kind: "image" as const,
  }));
  render(
    <ForwardDialog
      source={{
        kind: "agent",
        content: {
          text: "Image descriptions",
          attachments: [
            ...images,
            {
              filename: "report.pdf",
              url: "https://example.com/report.pdf",
              kind: "file",
            },
          ],
          localArtifacts: [],
        },
      }}
      onClose={vi.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "communication.forwardImagesOnly" }),
  );
  expect(
    screen.getByRole("checkbox", { name: "communication.forwardText" }),
  ).not.toBeChecked();
  expect(
    screen.getByRole("checkbox", { name: "report.pdf" }),
  ).not.toBeChecked();
  fireEvent.click(screen.getByRole("checkbox", { name: "first.png" }));
  fireEvent.change(
    screen.getByRole("textbox", { name: "communication.forwardNote" }),
    { target: { value: "Describe the selected image" } },
  );
  await choose("Analyst");
  fireEvent.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "communication.send",
    }),
  );
  await waitFor(() => expect(mock.send).toHaveBeenCalledTimes(1));
  expect(mock.start).toHaveBeenCalledWith(
    expect.anything(),
    expect.any(AbortSignal),
    {
      selection: {
        text: false,
        attachmentUrls: [images[1].url],
        localArtifactPaths: [],
      },
      note: "Describe the selected image",
    },
  );
});

it("selects a local file independently from local images and can restore all content", () => {
  render(
    <ForwardDialog
      source={{
        kind: "agent",
        content: {
          text: "Generated files",
          attachments: [],
          localArtifacts: [
            { path: "/work/chart.png", sha256: "a".repeat(64), size: 8 },
            { path: "/work/report.pdf", sha256: "b".repeat(64), size: 10 },
          ],
        },
      }}
      onClose={vi.fn()}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "communication.forwardFilesOnly" }),
  );
  expect(screen.getByRole("checkbox", { name: "report.pdf" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "chart.png" })).not.toBeChecked();
  expect(
    screen.getByRole("checkbox", { name: "communication.forwardText" }),
  ).not.toBeChecked();
  fireEvent.click(
    screen.getByRole("button", { name: "communication.forwardAll" }),
  );
  expect(
    screen
      .getAllByRole("checkbox")
      .every((input) => (input as HTMLInputElement).checked),
  ).toBe(true);
});

it("requires original content even when instructions and a recipient are provided", async () => {
  render(<ForwardDialog source={source} onClose={vi.fn()} />);
  fireEvent.click(
    screen.getByRole("checkbox", { name: "communication.forwardText" }),
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "communication.forwardNote" }),
    { target: { value: "Please summarize" } },
  );
  await choose("Analyst");
  expect(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "communication.send",
    }),
  ).toBeDisabled();
  expect(mock.start).not.toHaveBeenCalled();
});

it("locks content and instructions for a partial failure so retries keep the same draft", async () => {
  mock.send
    .mockRejectedValueOnce(new Error("temporary failure"))
    .mockImplementation(async (target: ForwardTarget) => ({ target }));
  render(<ForwardDialog source={source} onClose={vi.fn()} />);
  const note = screen.getByRole("textbox", {
    name: "communication.forwardNote",
  });
  fireEvent.change(note, { target: { value: "Prepare a summary" } });
  await choose("Analyst");
  const send = within(screen.getByRole("dialog")).getByRole("button", {
    name: "communication.send",
  });
  fireEvent.click(send);
  await screen.findByText("Analyst：temporary failure");
  await waitFor(() => expect(send).not.toHaveClass("ant-btn-loading"));
  expect(note).toBeDisabled();
  expect(
    screen.getByRole("checkbox", { name: "communication.forwardText" }),
  ).toBeDisabled();
  fireEvent.click(send);
  await waitFor(() => expect(mock.send).toHaveBeenCalledTimes(2));
  expect(mock.start).toHaveBeenCalledTimes(1);
});
