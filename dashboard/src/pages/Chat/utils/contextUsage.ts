import type { TokenUsage } from "../../../api/types";
import type {
  ContextUsageBreakdown,
  ContextUsageSegmentKey,
} from "../../../api/modules/octopThreads";

const SEGMENTS: ContextUsageSegmentKey[] = [
  "system_prompt",
  "tool_definitions",
  "rules",
  "skills",
  "mcp",
  "subagent_definitions",
  "conversation",
];

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function count(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? Math.floor(value)
    : 0;
}

/** Read the harness request stamp; segment estimates never replace provider totals. */
export function readContextBreakdown(
  value: unknown,
  maxTokens?: number,
): ContextUsageBreakdown | null {
  const raw = record(value);
  if (!raw) return null;
  const segments = Array.isArray(raw.segments)
    ? Object.fromEntries(
        raw.segments.flatMap((entry) => {
          const row = record(entry);
          return row && typeof row.key === "string"
            ? [[row.key, row.tokens]]
            : [];
        }),
      )
    : record(raw.segments) ?? {};
  const entries = SEGMENTS.map((key) => ({
    key,
    tokens: count(segments[key]),
  })).filter((entry) => entry.tokens > 0);
  const input = count(raw.input_tokens) || count(raw.used_tokens);
  if (!input || entries.length === 0) return null;
  const max = count(maxTokens) || count(raw.max_tokens) || 128000;
  return {
    max_tokens: max,
    used_tokens: Math.min(input, max),
    segments: entries,
  };
}

/** Tool rows often follow the latest AI message in a values snapshot. */
export function contextUsageFromState(messages: unknown): TokenUsage | null {
  if (!Array.isArray(messages)) return null;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const wire = record(messages[index]);
    if (!wire) continue;
    const message = record(wire.data) ?? record(wire.kwargs) ?? wire;
    const role = wire.type ?? message.type ?? message.role;
    if (
      role &&
      !["ai", "AIMessage", "AIMessageChunk", "assistant"].includes(String(role))
    )
      continue;
    const usage = record(message.usage_metadata);
    const rawStamp = record(record(message.additional_kwargs)?.context_usage);
    const stamp = readContextBreakdown(rawStamp);
    const input =
      count(usage?.input_tokens) ||
      count(usage?.prompt_tokens) ||
      count(rawStamp?.input_tokens) ||
      stamp?.used_tokens;
    if (input)
      return {
        input_tokens: input,
        ...(stamp ? { context_usage: { ...stamp, input_tokens: input } } : {}),
      };
  }
  return null;
}
