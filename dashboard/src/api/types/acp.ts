export type ACPToolParseMode = "call_title" | "update_detail" | "call_detail";

export const ACP_DEFAULT_STDIO_BUFFER_LIMIT_BYTES = 50 * 1024 * 1024;

export interface ACPRunnerConfig {
  enabled: boolean;
  command: string;
  args: string[];
  env: Record<string, string>;
  trusted: boolean;
  tool_parse_mode: ACPToolParseMode;
  stdio_buffer_limit_bytes: number;
}

export interface ACPConfig {
  tool_enabled: boolean;
  runners: Record<string, ACPRunnerConfig>;
}

export function parseArgsText(value: unknown): string[] {
  return String(value || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function parseEnvText(value: unknown): Record<string, string> {
  return String(value || "")
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean)
    .reduce<Record<string, string>>((acc, line) => {
      const index = line.indexOf("=");
      if (index >= 0) {
        const key = line.slice(0, index).trim();
        const envValue = line.slice(index + 1).trim();
        if (key) acc[key] = envValue;
      }
      return acc;
    }, {});
}

export function stringifyArgs(args: string[] = []): string {
  return args.join("\n");
}

export function stringifyEnv(env: Record<string, string> = {}): string {
  return Object.entries(env)
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");
}
