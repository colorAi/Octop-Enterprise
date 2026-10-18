import { request } from "../request";
import type { EnvVar } from "../types";

export const envsApi = {
  listEnvs: () => request<EnvVar[]>("/envs"),

  /** Batch save – full replacement of all env vars. */
  batchSaveEnvs: (envs: Record<string, string>) =>
    request<EnvVar[]>("/envs", {
      method: "PUT",
      body: JSON.stringify(envs),
    }),

  deleteEnv: (key: string) =>
    request<EnvVar[]>(`/envs/${encodeURIComponent(key)}`, {
      method: "DELETE",
    }),
};

// For backward compatibility
export const envApi = envsApi;
