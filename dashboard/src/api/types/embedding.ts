// Embedding API type definitions.

export type EmbeddingProvider = "none" | "local" | "custom";

export interface EmbeddingConfig {
  provider: EmbeddingProvider;
  localModel: string;
  apiKey: string;
  baseUrl: string;
  modelName: string;
  dimensions: number;
}

export type DownloadStatusType =
  | "idle"
  | "loading"
  | "downloading"
  | "done"
  | "failed";

export interface DownloadState {
  status: DownloadStatusType;
  progress: number;
  error?: string;
  model_name: string;
  downloaded_bytes: number;
  total_bytes?: number;
  speed_bytes_per_sec?: number;
  elapsed_seconds: number;
  eta_seconds?: number;
  applied_provider: EmbeddingProvider;
  applied_model_name: string;
  applied_dimensions?: number;
  applied_base_url: string;
  ready: boolean;
}
