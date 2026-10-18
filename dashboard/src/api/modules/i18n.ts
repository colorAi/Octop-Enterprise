import { request } from "../request";

export interface ToolLabelsResponse {
  locale: string;
  labels: Record<string, string>;
}

export const i18nApi = {
  getToolLabels: () => request<ToolLabelsResponse>("/i18n/tools"),
  getSkillLabels: () => request<ToolLabelsResponse>("/i18n/skills"),
};
