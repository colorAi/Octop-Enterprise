import { request } from "../request";

export interface ExpertAssignment {
  id: number;
  username: string;
  display_name: string | null;
  disabled: boolean;
  communication_user_id: string | null;
  mode: "legacy" | "assigned_only";
  revision: number;
  expert_ids: string[];
  skill_ids?: string[];
  scope_id?: string;
  membership_revision?: number;
  membership_status?: "current" | "changed" | "pending" | "unavailable";
}

export interface AssignedSkill {
  id: string;
  slug: string;
  name: string;
  description: string;
  category: string;
  kind: string;
  source_name: string;
  expert_ids: string[];
}

export interface DepartmentResources {
  scope_id: string;
  revision: number;
  expert_ids: string[];
  skill_ids: string[];
}

export interface EmployeeResourceSelection {
  id: number;
  mode: "legacy" | "assigned_only";
  expert_ids: string[];
  skill_ids: string[];
  expected_revision: number;
  scope_id: string;
  membership_revision: number;
}

export interface ExpertAssignmentOverview {
  source: { source_user_id: number | null; revision: number };
  administrators: {
    id: number;
    username: string;
    display_name: string | null;
  }[];
  experts: {
    agent_id: string;
    name: string;
    description: string | null;
    skill_ids?: string[];
    available?: boolean;
  }[];
  employees: ExpertAssignment[];
  skills?: AssignedSkill[];
  pools?: DepartmentResources[];
  defaults?: string[];
}

export type ExpertResourceCatalog = Pick<
  ExpertAssignmentOverview,
  "source" | "experts" | "skills" | "defaults"
>;

const root = "/admin/expert-assignments";
export const expertAssignmentsApi = {
  overview: (signal?: AbortSignal) =>
    request<ExpertAssignmentOverview>(root, { signal, cache: "no-store" }),
  directory: (signal?: AbortSignal) =>
    request<ExpertAssignmentOverview>(`${root}/directory`, {
      signal,
      cache: "no-store",
    }),
  catalog: (signal?: AbortSignal) =>
    request<ExpertResourceCatalog>(`${root}/catalog`, {
      signal,
      cache: "no-store",
    }),
  employee: (id: number, signal?: AbortSignal) =>
    request<ExpertAssignment>(`${root}/users/${id}`, {
      signal,
      cache: "no-store",
    }),
  source: (source_user_id: number, expected_revision: number) =>
    request<ExpertAssignmentOverview["source"]>(`${root}/source`, {
      method: "PUT",
      body: JSON.stringify({ source_user_id, expected_revision }),
    }),
  pool: (
    scopeId: string,
    body: Omit<DepartmentResources, "scope_id" | "revision"> & {
      expected_revision: number;
      expected_source_revision: number;
    },
  ) =>
    request<DepartmentResources & { affected_user_ids: number[] }>(
      `${root}/departments/${encodeURIComponent(scopeId)}`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
  defaults: (skill_ids: string[], expected_source_revision: number) =>
    request<{ skill_ids: string[] }>(`${root}/defaults`, {
      method: "PUT",
      body: JSON.stringify({ skill_ids, expected_source_revision }),
    }),
  employees: (
    employees: EmployeeResourceSelection[],
    expected_source_revision: number,
  ) =>
    request<{ employees: ExpertAssignment[] }>(`${root}/employees`, {
      method: "PUT",
      body: JSON.stringify({ employees, expected_source_revision }),
    }),
  basic: (
    id: number,
    body: {
      department_id: string | null;
      job_title_id: string | null;
      membership_revision: number;
      expected_revision: number;
      expected_source_revision: number;
    },
  ) =>
    request<ExpertAssignment>(`${root}/users/${id}/basic`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  save: (
    userId: number,
    body: {
      mode: "legacy" | "assigned_only";
      expert_ids: string[];
      expected_revision: number;
      expected_source_revision: number;
    },
  ) =>
    request<Pick<ExpertAssignment, "id" | "mode" | "revision" | "expert_ids">>(
      `${root}/users/${userId}`,
      { method: "PUT", body: JSON.stringify(body) },
    ),
};
