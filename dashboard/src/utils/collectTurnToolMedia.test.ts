import { describe, expect, it } from "vitest";
import type { AssistantTurnSplit } from "../pages/Chat/utils/messageContent";
import { collectTurnToolMedia } from "./collectTurnToolMedia";
import { agentMediaPreviewUrl, dedupeToolMedia } from "./toolMediaBlocks";

describe("generated image delivery", () => {
  it("shows one image when generation is followed by sending the same file", () => {
    const relative = "generated/images/a.png";
    const absolute = `/data/.octop/agents/A1/${relative}`;
    const split: AssistantTurnSplit = {
      tools: [
        {
          id: "generation",
          role: "assistant",
          content: "",
          timestamp: 1,
          toolData: {
            name: "generate_image",
            output: JSON.stringify({
              type: "image_gen_tool_result",
              images: [
                { path: relative, localPath: absolute, mediaType: "image/png" },
              ],
            }),
          },
          attachments: [
            {
              url: agentMediaPreviewUrl("A1", relative, "image/png"),
              kind: "image",
            },
          ],
        },
        {
          id: "delivery",
          role: "assistant",
          content: "",
          timestamp: 2,
          toolData: {
            name: "send_file_to_user",
            arguments: JSON.stringify({ file_path: absolute }),
            output: JSON.stringify({
              type: "image",
              filename: "a.png",
              source: {
                type: "url",
                url: agentMediaPreviewUrl("A1", absolute, "image/png"),
                media_type: "image/png",
              },
            }),
          },
        },
      ],
      thinkings: [],
      processSteps: [],
      answerMessage: null,
    };
    const live = collectTurnToolMedia(split, "A1");
    expect(live.images).toHaveLength(1);
    expect(live.images[0].url).toContain("source=generated%2Fimages%2Fa.png");
    // History reconstructs the gallery from tool outputs without WS attachments.
    const history = collectTurnToolMedia(
      {
        ...split,
        tools: split.tools.map(
          ({ attachments: _attachments, ...tool }) => tool,
        ),
      },
      "A1",
    );
    expect(history.images).toHaveLength(1);
    expect(collectTurnToolMedia(split, "A1").images).toHaveLength(1);
  });

  it("preserves different files and different agents with matching filenames", () => {
    const items = [
      {
        url: agentMediaPreviewUrl("A1", "generated/images/a.png", "image/png"),
      },
      {
        url: agentMediaPreviewUrl("A1", "generated/images/b.png", "image/png"),
      },
      {
        url: agentMediaPreviewUrl("A2", "generated/images/a.png", "image/png"),
      },
      { url: agentMediaPreviewUrl("A1", "/tmp/a.png", "image/png") },
      { url: "https://example.com/a.png" },
    ];
    expect(dedupeToolMedia(items)).toEqual(items);
  });
});
