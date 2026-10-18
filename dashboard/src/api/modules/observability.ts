import { request } from "../request";

export interface LangfuseConfig {
  enabled: boolean;
  public_key: string;
  host: string;
  secret_key_set: boolean;
  configured: boolean;
}

export const observabilityApi = {
  getLangfuse: () => request<LangfuseConfig>("/admin/observability/langfuse"),
  saveLangfuse: (body: {
    enabled: boolean;
    public_key: string;
    host: string;
    secret_key?: string | null;
  }) =>
    request<LangfuseConfig>("/admin/observability/langfuse", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  testLangfuse: (body?: {
    public_key?: string | null;
    host?: string | null;
    secret_key?: string | null;
  }) =>
    request<{ ok: boolean; error?: string }>(
      "/admin/observability/langfuse/test",
      {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      },
    ),
};
