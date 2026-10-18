import type { TFunction } from "i18next";
import type { SlashClientAction, SlashCommandSpec } from "../api/modules/slash";

export interface SlashFallbackMeta {
  name: string;
  command: string;
  aliases: string[];
  usage: string;
  icon: string;
  tone: string;
  category: string;
  origins: string[];
  client_action: SlashClientAction;
}

export const SLASH_FALLBACK_META: SlashFallbackMeta[] = [
  {
    name: "status",
    command: "/status",
    aliases: [],
    usage: "/status",
    icon: "Activity",
    tone: "blue",
    category: "core",
    origins: ["all"],
    client_action: "none",
  },
  {
    name: "compact",
    command: "/compact",
    aliases: [],
    usage: "/compact",
    icon: "Archive",
    tone: "violet",
    category: "core",
    origins: ["all"],
    client_action: "none",
  },
  {
    name: "new",
    command: "/new",
    aliases: ["clear"],
    usage: "/new",
    icon: "RefreshCw",
    tone: "emerald",
    category: "core",
    origins: ["all"],
    client_action: "new_chat",
  },
  {
    name: "history",
    command: "/history",
    aliases: [],
    usage: "/history",
    icon: "BarChart3",
    tone: "blue",
    category: "core",
    origins: ["all"],
    client_action: "none",
  },
  {
    name: "list",
    command: "/list",
    aliases: ["topics", "sessions"],
    usage: "/list",
    icon: "List",
    tone: "amber",
    category: "session",
    origins: ["all"],
    client_action: "none",
  },
  {
    name: "token",
    command: "/token",
    aliases: [],
    usage: "/token",
    icon: "Coins",
    tone: "orange",
    category: "core",
    origins: ["all"],
    client_action: "none",
  },
];

export function buildFallbackSlashCommands(t: TFunction): SlashCommandSpec[] {
  return SLASH_FALLBACK_META.map((meta) => ({
    ...meta,
    label_en: t(`slash.fallback.${meta.name}.label`, { lng: "en" }),
    label_zh: t(`slash.fallback.${meta.name}.label`, { lng: "zh" }),
    description_en: t(`slash.fallback.${meta.name}.description`, { lng: "en" }),
    description_zh: t(`slash.fallback.${meta.name}.description`, { lng: "zh" }),
  }));
}
