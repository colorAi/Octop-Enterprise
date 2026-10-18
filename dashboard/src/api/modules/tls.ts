import { request } from "../request";

export interface PreflightCheck {
  id: string;
  ok: boolean;
  message: string;
}

export interface PreflightResult {
  ok: boolean;
  checks: PreflightCheck[];
  renewal?: boolean;
}

export interface TlsStatus {
  tls: {
    enabled: boolean;
    mode: string;
    domains: string[];
    issued_at: string;
    expires_at: string;
    acme_staging: boolean;
    http_port: number;
    https_port: number | null;
    dual_listeners: boolean;
    cert_present: boolean;
  };
  task: {
    state: string;
    domain: string;
    error: string;
    steps: string[];
    staging: boolean;
  };
  eligible: boolean;
  issue_mode: string | null;
  renewal: boolean;
}

export const tlsApi = {
  getStatus: () => request<TlsStatus>("/admin/tls/status"),
  preflight: (domain: string) =>
    request<PreflightResult>("/admin/tls/preflight", {
      method: "POST",
      body: JSON.stringify({ domain }),
    }),
  issue: (domain: string, staging = false) =>
    request<PreflightResult>("/admin/tls/issue", {
      method: "POST",
      body: JSON.stringify({ domain, staging }),
    }),
};
