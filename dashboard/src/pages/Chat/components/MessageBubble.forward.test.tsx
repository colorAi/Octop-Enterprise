import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import MessageBubble from "./MessageBubble";
import AssistantTurnView from "./AssistantTurnView";
import type { ChatMessage } from "../hooks/useChat";
import type { DesktopRuntimeBridge } from "../../../api/desktopRuntime";

const mock = vi.hoisted(() => ({
  user: { role: "user", username: "alice", permissions: ["messaging"] },
  authImage: vi.fn((url: string) => ({ src: url, loadState: "ready" })),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => mock.user,
}));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../../context/VoiceOutputContext", () => ({
  useVoiceOutputContext: () => ({ speak: vi.fn(), speakingId: null }),
}));
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    activeAgentId: "a",
    activeAgent: { agent_id: "a", name: "Helper" },
    agents: [],
  }),
}));
vi.mock("../../../hooks/useAuthImageSrc", () => ({
  useAuthImageSrc: mock.authImage,
}));

const message = (extra: Partial<ChatMessage> = {}): ChatMessage => ({
  id: "answer",
  role: "assistant",
  content: "Answer",
  timestamp: 0,
  status: "done",
  ...extra,
});
beforeEach(() => {
  mock.user.permissions = ["messaging"];
  mock.authImage.mockClear();
});
afterEach(() => {
  delete window.__octopRuntime;
});

it("previews forwarded desktop images through the native receipt and hides duplicate file hints", async () => {
  const path = "C:\\work\\inbound\\photo.png";
  const file = { path, size: 42, sha256: "a".repeat(64) };
  const png = "data:image/png;base64,eA==";
  const previewImage = vi
    .fn()
    .mockResolvedValue({ data_url: png, width: 1, height: 1 });
  window.__octopRuntime = {
    version: 1,
    previewImage,
  } as unknown as DesktopRuntimeBridge;
  render(
    <MessageBubble
      message={message({
        role: "user",
        content: `修改这张图\n\n[file: ${path}]`,
        attachments: [
          {
            url: "",
            filename: "photo.png",
            workspacePath: path,
            kind: "image",
            localFile: file,
          },
        ],
      })}
      agentId="a"
    />,
  );
  expect(
    await screen.findByRole("img", { name: "本地图片预览：photo.png" }),
  ).toHaveAttribute("src", png);
  expect(previewImage).toHaveBeenCalledWith({ path, sha256: file.sha256 });
  expect(mock.authImage.mock.calls.some(([url]) => Boolean(url))).toBe(false);
  expect(screen.getByText("修改这张图")).toBeInTheDocument();
  expect(screen.queryByText(/\[file:/)).toBeNull();
});

it("shows forwarding under both employee prompts and agent answers", () => {
  render(
    <>
      <MessageBubble message={message({ role: "user" })} agentId="a" />
      <MessageBubble message={message()} agentId="a" />
    </>,
  );
  expect(
    screen.getAllByRole("button", { name: "communication.forward" }),
  ).toHaveLength(2);
});

it("exposes forwarding for attachment-only responses without a timestamp", () => {
  render(
    <MessageBubble
      message={message({
        content: "",
        attachments: [
          {
            url: "data:image/png;base64,eA==",
            filename: "image.png",
            kind: "image",
          },
        ],
      })}
      agentId="a"
    />,
  );
  expect(
    screen.getByRole("button", { name: "communication.forward" }),
  ).toBeEnabled();
});

it("keeps unfinished, failed and pending steer messages out of forwarding", () => {
  render(
    <>
      <MessageBubble message={message({ status: "streaming" })} />
      <MessageBubble message={message({ status: "error" })} />
      <MessageBubble message={message({ role: "user", steerPending: true })} />
    </>,
  );
  expect(
    screen.queryByRole("button", { name: "communication.forward" }),
  ).toBeNull();
});

it("requires enterprise messaging permission", () => {
  mock.user.permissions = [];
  render(<MessageBubble message={message()} agentId="a" />);
  expect(
    screen.queryByRole("button", { name: "communication.forward" }),
  ).toBeNull();
});

it("offers forwarding when a turn only delivers generated local files", () => {
  render(
    <AssistantTurnView
      agentId="a"
      messages={[
        message({
          content: "",
          localArtifacts: [
            {
              path: "/work/project/report.pdf",
              size: 10,
              sha256: "a".repeat(64),
            },
          ],
        }),
      ]}
    />,
  );
  expect(
    screen.getByRole("button", { name: "communication.forward" }),
  ).toBeEnabled();
});

it("waits for the entire agent turn to settle before forwarding its deliverables", () => {
  render(
    <AssistantTurnView
      agentId="a"
      isTurnInProgress
      messages={[
        message({
          content: "",
          localArtifacts: [
            {
              path: "/work/project/report.pdf",
              size: 10,
              sha256: "a".repeat(64),
            },
          ],
        }),
      ]}
    />,
  );
  expect(
    screen.getByRole("button", { name: "communication.forward" }),
  ).toBeDisabled();
});
