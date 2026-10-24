import { request } from "../request";
import type { OctopAgent } from "../../context/AgentContext";

export interface UserRow {
  id: number;
  username: string;
  /** Role-template public id: admin | user | custom ULID. */
  role: string;
  display_name: string | null;
  email?: string | null;
  has_password?: boolean;
  sso_linked?: boolean;
  disabled: boolean;
  login_failed_count?: number;
  login_locked?: boolean;
  login_locked_until?: number;
  login_retry_after_seconds?: number;
  created_at?: number;
  permissions?: string[];
  role_name?: string | null;
  avatar_url?: string | null;
  avatar_icon?: string | null;
  workspace_root_dir?: string | null;
  token_quota?: number | null;
  max_agents?: number | null;
  agent_count: number;
}

export interface UserPage {
  items: UserRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface EmployeeKnowledgeAccess {
  knowledge_base_ids: string[];
  items: {
    id: string;
    name: string;
    description: string;
    shared: boolean;
  }[];
}

export interface EmployeeModelSelection {
  model_refs: string[];
  expected_revision: string;
}

export interface EmployeeModelAccess {
  model_refs: string[];
  revision: string;
  items: { ref: string; provider_name: string; model: string; name: string }[];
}

export const usersApi = {
  models: (userId: number, signal: AbortSignal) =>
    request<EmployeeModelAccess>(`/users/${userId}/models`, {
      signal,
      cache: "no-store",
    }),
  setModels: (userId: number, selection: EmployeeModelSelection) =>
    request<EmployeeModelAccess>(`/users/${userId}/models`, {
      method: "PUT",
      body: JSON.stringify(selection),
    }),
  knowledgeBases: (userId: number, signal: AbortSignal) =>
    request<EmployeeKnowledgeAccess>(`/users/${userId}/knowledge-bases`, {
      signal,
      cache: "no-store",
    }),
  setKnowledgeBases: (userId: number, knowledgeBaseIds: string[]) =>
    request<EmployeeKnowledgeAccess>(`/users/${userId}/knowledge-bases`, {
      method: "PUT",
      body: JSON.stringify({ knowledge_base_ids: knowledgeBaseIds }),
    }),
  page: (
    page: number,
    pageSize: number,
    search: string,
    signal: AbortSignal,
  ) => {
    const query = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
      search,
    });
    return request<UserPage>(`/users/page?${query}`, { signal });
  },
  agents: (userId: number, signal: AbortSignal) =>
    request<OctopAgent[]>(`/agents?scope=all&owner_user_id=${userId}`, {
      signal,
    }),
};
