import type { SlashCommandSpec } from "../api/modules/slash";

/** Expert welcome card / quick prompt that still needs user content before send. */
export function promptNeedsUserInput(text: string): boolean {
  const trimmed = text.trimEnd();
  if (!trimmed) return false;

  // A trailing colon invites the user to fill in a value, such as "The goal is:".
  if (/[：:]\s*$/.test(trimmed)) return true;

  // Empty inline fields, such as "Recipient:, Purpose:".
  if (/[：:]\s*[，,、]/.test(trimmed)) return true;

  // Ends with blank lines — paste content below
  if (/\n\s*\n\s*$/.test(text)) return true;

  // Empty fenced code block placeholder
  if (/```\s*\n\s*```/.test(text)) return true;

  return false;
}

/** Slash usage declares a required `<arg>` (not optional `[arg]`). */
export function slashCommandNeedsInput(spec: SlashCommandSpec): boolean {
  const usage = spec.usage || spec.command;
  return /<[^>]+>/.test(usage);
}

/** Text to insert when a parameterized slash command is picked from the menu. */
export function slashCommandPrefillText(spec: SlashCommandSpec): string {
  const usage = spec.usage || spec.command;
  if (usage.includes("switch <")) {
    return spec.command === "/agent" ? "/agent switch " : `${spec.command} `;
  }
  return `${spec.command} `;
}
