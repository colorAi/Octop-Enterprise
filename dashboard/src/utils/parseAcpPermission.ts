export interface AcpPermissionOption {
  id: string;
  title: string;
}

export interface AcpPermissionPrompt {
  title: string;
  runner: string;
  options: AcpPermissionOption[];
}

const PERMISSION_HEADER = /^\[permission_required\]\s*(.*)$/m;
const OPTION_LINE = /^-\s+(.+?)(?:\s+\(id=([^)]+)\))?\s*$/;

function parseRunnerFromArguments(argumentsJson: string | undefined): string {
  if (!argumentsJson) return "";
  try {
    const parsed = JSON.parse(argumentsJson) as Record<string, unknown>;
    const runner = parsed.runner;
    return typeof runner === "string" ? runner.trim() : "";
  } catch {
    return "";
  }
}

/** Parse ``acp_runner`` tool output for an external permission prompt. */
export function parseAcpPermissionPrompt(
  output: string | undefined,
  argumentsJson: string | undefined,
): AcpPermissionPrompt | null {
  if (!output || !output.includes("[permission_required]")) {
    return null;
  }

  const titleMatch = output.match(PERMISSION_HEADER);
  const title = titleMatch?.[1]?.trim() || "permission request";
  const runner = parseRunnerFromArguments(argumentsJson);

  const options: AcpPermissionOption[] = [];
  for (const line of output.split("\n")) {
    const match = line.trim().match(OPTION_LINE);
    if (!match) continue;
    const label = match[1]?.trim() || "option";
    const id = match[2]?.trim() || label;
    options.push({ id, title: label });
  }

  if (options.length === 0) {
    return null;
  }

  return { title, runner, options };
}

/** User message instructing the agent to call ``acp_runner`` respond. */
export function buildAcpPermissionRespondMessage(
  runner: string,
  optionId: string,
): string {
  const runnerPart = runner ? `runner="${runner}", ` : "";
  return (
    `请调用 acp_runner 回应外部 ACP 权限请求：` +
    `action="respond", ${runnerPart}message="${optionId}"`
  );
}
