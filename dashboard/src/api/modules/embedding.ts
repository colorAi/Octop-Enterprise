import { request } from "../request";
import type { EmbeddingConfig, DownloadState } from "../types/embedding";

export const embeddingApi = {
  // GET /api/embedding/config
  getConfig: () => request<EmbeddingConfig>("/embedding/config"),

  // GET /api/embedding/models — fetch preset model list.
  getPresetModels: () => request<string[]>("/embedding/models"),

  // PUT /api/embedding/config
  updateConfig: (config: EmbeddingConfig) =>
    request<EmbeddingConfig>("/embedding/config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  // POST /api/embedding/apply
  applyConfig: (config: EmbeddingConfig) =>
    request<EmbeddingConfig>("/embedding/apply", {
      method: "POST",
      body: JSON.stringify(config),
    }),

  // POST /api/embedding/download-local
  downloadLocalModel: (modelName: string) =>
    request<{ message: string; model: string }>("/embedding/download-local", {
      method: "POST",
      body: JSON.stringify({ modelName }),
    }),

  // GET /api/embedding/download-status
  getDownloadStatus: () =>
    request<DownloadState>("/embedding/download-status", {
      cache: "no-store",
    }),

  // POST /api/embedding/delete-local-model
  deleteLocalModel: (modelName: string) =>
    request<{
      message: string;
      model: string;
      deleted: boolean;
      removed_path?: string;
    }>("/embedding/delete-local-model", {
      method: "POST",
      body: JSON.stringify({ modelName }),
    }),
};
