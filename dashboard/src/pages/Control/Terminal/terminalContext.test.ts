import { describe, expect, it } from "vitest";
import {
  extractBashBlocks,
  formatTerminalUserMessage,
  isShellLanguage,
  outputLooksFailed,
} from "./terminalContext";

describe("terminalContext", () => {
  const labels = {
    os: "OS",
    shell: "Shell",
    hostname: "Host",
    user: "User",
    cwd: "CWD",
  };

  it("formatTerminalUserMessage prefixes context and autopilot tag", () => {
    const msg = formatTerminalUserMessage(
      {
        os: "Darwin",
        distro: "macOS",
        shell: "zsh",
        hostname: "host",
        username: "u",
        workspace_dir: "/tmp/ws",
        agent_id: "a",
        agent_name: "n",
      },
      "check disk",
      labels,
      { autopilot: true, includeContext: true },
    );
    expect(msg).toContain("[AUTOPILOT]");
    expect(msg).toContain("check disk");
    expect(msg).toContain("/tmp/ws");
  });

  it("formatTerminalUserMessage skips context when includeContext is false", () => {
    const msg = formatTerminalUserMessage(
      {
        os: "Darwin",
        distro: "macOS",
        shell: "zsh",
        hostname: "host",
        username: "u",
        workspace_dir: "/tmp/ws",
        agent_id: "a",
        agent_name: "n",
      },
      "follow up",
      labels,
      { includeContext: false },
    );
    expect(msg).toBe("follow up");
    expect(msg).not.toContain("[Terminal context]");
  });

  it("extractBashBlocks preserves order", () => {
    const md = "plan\n\n```bash\ndf -h\n```\n\n```sh\nls\n```";
    expect(extractBashBlocks(md)).toEqual(["df -h", "ls"]);
  });

  it("isShellLanguage recognizes shell tags", () => {
    expect(isShellLanguage("bash")).toBe(true);
    expect(isShellLanguage("python")).toBe(false);
  });

  it("outputLooksFailed detects common errors", () => {
    expect(outputLooksFailed("bash: foo: command not found")).toBe(true);
    expect(outputLooksFailed("total 0")).toBe(false);
  });
});
