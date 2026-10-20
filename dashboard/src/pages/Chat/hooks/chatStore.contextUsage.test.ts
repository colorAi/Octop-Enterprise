import { afterEach, expect, it } from "vitest";
import * as store from "./chatStore";

const SESSION = "context-breakdown";
afterEach(() => store.removeSession(SESSION));

it("retains context composition from native snapshots when a tool result is last", () => {
  store.ingestHarnessChunk(SESSION, {
    type: "state_snapshot",
    data: {
      messages: [
        {
          type: "ai",
          usage_metadata: { input_tokens: 12000 },
          additional_kwargs: {
            context_usage: {
              max_tokens: 128000,
              used_tokens: 12000,
              input_tokens: 12000,
              segments: {
                system_prompt: 2000,
                tool_definitions: 5000,
                conversation: 5000,
              },
            },
          },
        },
        { type: "tool", content: "Tool completed" },
      ],
    },
  });
  expect(store.getSnapshot(SESSION).contextUsage).toMatchObject({
    input_tokens: 12000,
    context_usage: {
      segments: [
        { key: "system_prompt", tokens: 2000 },
        { key: "tool_definitions", tokens: 5000 },
        { key: "conversation", tokens: 5000 },
      ],
    },
  });
});
