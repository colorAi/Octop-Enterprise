import { request } from "../request";

export interface ClientConfigStatus {
  resource_update_pending?: boolean;
  expert_access_mode?: "legacy" | "assigned_only";
  user_id: number;
  role: string;
  permissions: string[];
  revision: string;
  message: string;
  published_at: number | null;
}

export const clientConfigApi = {
  apply: () =>
    request<ClientConfigStatus>("/client-config/apply", { method: "POST" }),
  status: () =>
    request<ClientConfigStatus>("/client-config/status", { cache: "no-store" }),
  publish: (message: string) =>
    request<{ id: string; published_at: number; message: string }>(
      "/client-config/publish",
      {
        method: "POST",
        body: JSON.stringify({ message }),
      },
    ),
};
