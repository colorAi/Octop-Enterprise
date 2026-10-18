import { expect, it } from "vitest";
import { agentForwardContent } from "./forwardContent";
import type { ChatMessage } from "../Chat/hooks/useChat";

const msg = (extra: Partial<ChatMessage>): ChatMessage => ({
  id: "answer",
  role: "assistant",
  content: "",
  status: "done",
  timestamp: 1,
  ...extra,
});

it("re-forwards an imported user image as a local receipt instead of an empty server URL", () => {
  const file = {
    path: "C:\\work\\inbound\\image.png",
    size: 42,
    sha256: "a".repeat(64),
  };
  expect(
    agentForwardContent(
      [
        msg({
          role: "user",
          content: `继续修改\n\n[file: ${file.path}]`,
          attachments: [
            {
              url: "",
              filename: "image.png",
              workspacePath: file.path,
              kind: "image",
              localFile: file,
            },
          ],
        }),
      ],
      "a",
    ),
  ).toEqual({ text: "继续修改", attachments: [], localArtifacts: [file] });
});

it("forwards visible answer content and workspace deliverables without reasoning or tool logs", () => {
  const url = "/api/agents/a/workspace/download?path=outbound%2Freport.pdf";
  const content = agentForwardContent(
    [
      msg({
        content: `<think>private reasoning</think>Here is [report.pdf](${url})`,
      }),
      msg({
        id: "tool",
        toolData: {
          name: "write_file",
          arguments: '{"path":"outbound/report.pdf"}',
          output: "tool log",
        },
      }),
    ],
    "a",
  );
  expect(content.text).toBe("Here is report.pdf");
  expect(content.attachments).toHaveLength(1);
  expect(content.attachments[0].url).toContain("report.pdf");
});

it("deduplicates image previews and generated local receipts while retaining the exact hash", () => {
  const local = {
    path: "/work/generated/a.png",
    size: 10,
    sha256: "a".repeat(64),
  };
  const content = agentForwardContent(
    [
      msg({
        content: "Ready",
        attachments: [
          {
            kind: "image",
            workspacePath: "generated/a.png",
            url: "/api/agents/a/media/preview?source=generated%2Fa.png",
            filename: "a.png",
          },
        ],
      }),
      msg({
        id: "receipt",
        localArtifacts: [local],
        content: "本次任务生成的本地文件",
      }),
    ],
    "a",
  );
  expect(content).toEqual({
    text: "Ready",
    attachments: [],
    localArtifacts: [local],
  });
});

it("includes attachment-only answers and keeps external web links in the text", () => {
  const image = {
    kind: "image" as const,
    url: "data:image/png;base64,eA==",
    filename: "image.png",
  };
  const content = agentForwardContent(
    [
      msg({ attachments: [image] }),
      msg({ content: "[Source](https://example.com)" }),
    ],
    "a",
  );
  expect(content.attachments).toEqual([image]);
  expect(content.text).toBe("[Source](https://example.com)");
});

it("does not forward errors, pending approvals or standalone reasoning", () => {
  const content = agentForwardContent(
    [
      msg({ content: "failed", status: "error" }),
      msg({ content: "secret reasoning", type: "reasoning" }),
      msg({
        content: "approve?",
        hitlData: { action_requests: [], status: "pending" },
      }),
      msg({
        content: "visible",
        contentBlocks: [
          { type: "thinking", content: "hidden" },
          { type: "text", content: "visible" },
        ],
      }),
    ],
    "a",
  );
  expect(content.text).toBe("visible");
});

it("copies relative document links and inline remote images instead of leaving inaccessible links", () => {
  const content = agentForwardContent(
    [
      msg({
        content:
          "[Download](outbound/report.pdf)\n![A picture](https://example.com/photo.png)",
      }),
    ],
    "a",
  );
  expect(content.text).toBe("Download\nA picture");
  expect(content.attachments).toHaveLength(2);
  expect(content.attachments[0].filename).toBe("report.pdf");
  expect(content.attachments[1].url).toBe("https://example.com/photo.png");
});

it("forwards a generated image once even if a later tool delivers the same absolute path", () => {
  const relative = "generated/images/a.png";
  const absolute = "/data/.octop/agents/a/generated/images/a.png";
  const content = agentForwardContent(
    [
      msg({
        toolData: {
          name: "generate_image",
          output: JSON.stringify({
            type: "image_gen_tool_result",
            images: [
              { path: relative, localPath: absolute, mediaType: "image/png" },
            ],
          }),
        },
      }),
      msg({
        id: "delivery",
        toolData: {
          name: "send_file_to_user",
          arguments: JSON.stringify({ file_path: absolute }),
          output: JSON.stringify({
            type: "image",
            filename: "a.png",
            source: {
              type: "url",
              url:
                "/api/agents/a/media/preview?source=" +
                encodeURIComponent(absolute),
              media_type: "image/png",
            },
          }),
        },
      }),
    ],
    "a",
  );
  expect(content.attachments).toHaveLength(1);
});
