import { request } from "../request";

export interface ClientConfigStatus {
  user_id: number;
  role: string;
  permissions: string[];
  revision: string;
  message: string;
  published_at: number | null;
}

export const clientConfigApi = {
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
