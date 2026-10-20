import { expect, it } from "vitest";
import { contextUsageFromState, readContextBreakdown } from "./contextUsage";

it("preserves the uncapped input when the chosen model has a larger context window", () => {
  const usage = contextUsageFromState([
    {
      type: "ai",
      usage_metadata: { input_tokens: 200000 },
      additional_kwargs: {
        context_usage: {
          max_tokens: 128000,
          used_tokens: 128000,
          input_tokens: 200000,
          segments: { tool_definitions: 5000, conversation: 190000 },
        },
      },
    },
  ]);
  expect(usage?.input_tokens).toBe(200000);
  expect(readContextBreakdown(usage?.context_usage, 262144)?.used_tokens).toBe(
    200000,
  );
});

it("keeps provider totals independent of estimated segment totals", () => {
  const result = readContextBreakdown({
    input_tokens: 10000,
    segments: { tool_definitions: 3000, conversation: 8000 },
  });
  expect(result?.used_tokens).toBe(10000);
  expect(result?.segments.reduce((sum, item) => sum + item.tokens, 0)).toBe(
    11000,
  );
});

it("unwraps serialized messages and ignores invalid segment counts", () => {
  const usage = contextUsageFromState([
    {
      type: "ai",
      data: {
        additional_kwargs: {
          context_usage: {
            input_tokens: 10000,
            segments: {
              tool_definitions: 5000,
              skills: -100,
              conversation: Number.NaN,
              unknown: 999,
            },
          },
        },
      },
    },
  ]);
  expect(readContextBreakdown(usage?.context_usage)?.segments).toEqual([
    { key: "tool_definitions", tokens: 5000 },
  ]);
});

it("does not reuse an older category stamp for a newer model response", () => {
  const usage = contextUsageFromState([
    {
      type: "ai",
      additional_kwargs: {
        context_usage: {
          input_tokens: 100,
          segments: { tool_definitions: 100 },
        },
      },
    },
    { type: "ai", usage_metadata: { input_tokens: 200 } },
    { type: "tool", content: "result" },
  ]);
  expect(usage).toEqual({ input_tokens: 200 });
  expect(readContextBreakdown(usage?.context_usage)).toBeNull();
});
