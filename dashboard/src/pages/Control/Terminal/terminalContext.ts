import type { TerminalContext } from "../../../api/modules/terminalAi";

export interface TerminalContextLabels {
  os: string;
  shell: string;
  hostname: string;
  user: string;
  cwd: string;
}

export {
  extractBashBlocks,
  isShellLanguage,
} from "../../../utils/shellCodeBlock";

/** Format server terminal context as a compact prefix (not shown in the UI input). */
export function formatTerminalContextBlock(
  ctx: TerminalContext,
  labels: TerminalContextLabels,
): string {
  const lines = [
    `[Terminal context]`,
    `${labels.os}: ${ctx.distro || ctx.os}`,
    `${labels.shell}: ${ctx.shell}`,
    `${labels.hostname}: ${ctx.hostname}`,
    `${labels.user}: ${ctx.username}`,
    `${labels.cwd}: ${ctx.workspace_dir}`,
  ];
  return lines.join("\n");
}

/**
 * Prefix user text with terminal context for the ops-engineer agent.
 * workspace_dir is the agent workspace path, not necessarily the live PTY cwd.
 */
export function formatTerminalUserMessage(
  ctx: TerminalContext | null,
  userText: string,
  labels: TerminalContextLabels,
  options?: { autopilot?: boolean; includeContext?: boolean },
): string {
  const trimmed = userText.trim();
  if (!trimmed) return trimmed;

  const parts: string[] = [];
  if (options?.autopilot) {
    parts.push("[AUTOPILOT]");
  }
  if (ctx && options?.includeContext !== false) {
    parts.push(formatTerminalContextBlock(ctx, labels));
  }
  parts.push(trimmed);
  return parts.join("\n\n");
}

const FAILURE_PATTERNS = [
  /command not found/i,
  /permission denied/i,
  /no such file or directory/i,
  /syntax error/i,
  /error:/i,
];

export function outputLooksFailed(output: string): boolean {
  return FAILURE_PATTERNS.some((p) => p.test(output));
}
