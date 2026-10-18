import { request } from "../request";

export type SlashClientAction =
  | "none"
  | "new_chat"
  | "cancel_stream"
  | "switch_agent";

export interface SlashCommandSpec {
  name: string;
  command: string;
  aliases: string[];
  label_en: string;
  label_zh: string;
  description_en: string;
  description_zh: string;
  usage: string;
  icon: string;
  tone: string;
  category: string;
  origins: string[];
  client_action: SlashClientAction;
}

export interface SlashCommandListResponse {
  origin: string;
  commands: SlashCommandSpec[];
}

export const slashApi = {
  listCommands: (origin = "ui") =>
    request<SlashCommandListResponse>(
      `/slash/commands?origin=${encodeURIComponent(origin)}`,
    ),
};
