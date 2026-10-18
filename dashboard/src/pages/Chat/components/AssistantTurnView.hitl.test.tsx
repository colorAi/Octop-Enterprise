import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "@/api/desktopRuntime";
import type { ChatMessage } from "../hooks/useChat";
import AssistantTurnView from "./AssistantTurnView";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, opts?: string | { defaultValue?: string }) => {
      if (typeof opts === "string") return opts;
      return opts?.defaultValue ?? key;
    },
  }),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ username: "ada", display_name: "Ada" }),
}));

vi.mock("../../../context/VoiceOutputContext", () => ({
  useVoiceOutputContext: () => ({ speakingId: null, speak: vi.fn() }),
}));

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    activeAgentId: "host",
    activeAgent: {
      agent_id: "host",
      name: "主持团队",
      kind: "team",
      icon_name: "bot",
    },
    agents: [
      {
        agent_id: "host",
        name: "主持团队",
        kind: "team",
        icon_name: "bot",
      },
    ],
  }),
}));

function message(
  extra: Partial<ChatMessage> & Pick<ChatMessage, "id">,
): ChatMessage {
  return {
    role: "assistant",
    content: "",
    status: "done",
    timestamp: 1,
    ...extra,
  };
}

afterEach(() => {
  delete window.__octopRuntime;
});

it("shows delivered images with a file action and hides redundant path metadata", async () => {
  const file = {
    path: "/work/thread/generated/golden-hour.png",
    size: 123,
    sha256: "a".repeat(64),
  };
  const previewImage = vi.fn().mockResolvedValue({
    data_url: "data:image/png;base64,aW1hZ2U=",
    width: 12,
    height: 8,
  });
  const reveal = vi.fn().mockResolvedValue(undefined);
  window.__octopRuntime = {
    version: 1,
    previewImage,
    reveal,
  } as unknown as DesktopRuntimeBridge;
  render(
    <AssistantTurnView
      messages={[
        message({
          id: "answer",
          content: "图生成好了：generated/golden-hour.png",
          contentBlocks: [
            {
              type: "text",
              content: "图生成好了：`generated/golden-hour.png`",
            },
          ],
        }),
        message({
          id: "receipt",
          content: "本次任务生成的本地文件",
          localArtifacts: [file],
        }),
      ]}
    />,
  );
  expect(
    await screen.findByRole("img", { name: "本地图片预览：golden-hour.png" }),
  ).toBeInTheDocument();
  expect(screen.queryByText(file.path)).not.toBeInTheDocument();
  expect(
    screen.queryByText("generated/golden-hour.png"),
  ).not.toBeInTheDocument();
  expect(screen.queryByText(/SHA-256/)).not.toBeInTheDocument();
  expect(screen.queryByText("本地任务成果")).not.toBeInTheDocument();
  expect(screen.getByText("图生成好了")).toBeInTheDocument();
  expect(previewImage).toHaveBeenCalledWith({
    path: file.path,
    sha256: file.sha256,
  });
  fireEvent.click(screen.getByRole("button", { name: "打开文件位置" }));
  expect(reveal).toHaveBeenCalledWith({ path: file.path, sha256: file.sha256 });
});

describe("AssistantTurnView approval", () => {
  it("keeps the answer that arrived before the approval card", () => {
    render(
      <AssistantTurnView
        messages={[
          message({ id: "text", content: "磁盘还剩 8G" }),
          message({
            id: "tool",
            toolData: { name: "execute", arguments: "{}" },
          }),
          message({
            id: "card",
            hitlData: {
              action_requests: [{ name: "execute", args: { command: "df" } }],
              status: "pending",
            },
          }),
        ]}
      />,
    );

    expect(screen.getByText("磁盘还剩 8G")).toBeInTheDocument();
    expect(screen.getByText("Confirm this action")).toBeInTheDocument();
  });
});
