export type SkillKind = "builtin" | "workspace";

export interface SkillListItem {
  slug: string;
  tool_name: string;
  description: string;
  kind: SkillKind;
  enabled?: boolean;
  has_references?: boolean;
  has_scripts?: boolean;
}

export interface SkillDetail extends SkillListItem {
  content: string;
  path: string;
  references?: Record<string, unknown>;
  scripts?: Record<string, unknown>;
}

export type SkillSpec = SkillListItem;

export interface SkillFormValues {
  name: string;
  content: string;
  source?: string;
  path?: string;
}

export interface HubSkillSpec {
  slug: string;
  name: string;
  description: string;
  version: string;
  source_url: string;
}

export interface SkillHubSkill {
  slug: string;
  name: string;
  description: string;
  version: string;
}

// Legacy Skill interface for backward compatibility
export interface Skill {
  id: string;
  name: string;
  description: string;
  function_name: string;
  enabled: boolean;
  version: string;
  tags: string[];
  created_at: number;
  updated_at: number;
}
