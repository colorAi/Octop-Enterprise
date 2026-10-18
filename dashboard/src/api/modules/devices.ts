import { request } from "../request";

export interface DesktopDevice {
  device_id: string;
  name: string;
  platform: "windows" | "darwin";
  protocol_version: number;
  online: boolean;
  capabilities: string[];
  grants?: DirectoryGrant[];
  expires_at: number;
  revoked_at: number | null;
}
export interface DeviceList {
  enabled: boolean;
  devices: DesktopDevice[];
}
export interface PairingCode {
  code: string;
  expires_at: number;
  credential_expires_at: number;
}

export const devicesApi = {
  list: (signal?: AbortSignal) => request<DeviceList>("/devices", { signal }),
  pair: () => request<PairingCode>("/devices/pairing", { method: "POST" }),
  revoke: (id: string) =>
    request<void>(`/devices/${encodeURIComponent(id)}`, { method: "DELETE" }),
  probe: (id: string) =>
    request<{
      call_id: string;
      status: "completed" | "timed_out" | "cancelled" | "device_disconnected";
    }>(`/devices/${encodeURIComponent(id)}/probe`, { method: "POST" }),
};

export interface DirectoryGrant {
  id: string;
  name: string;
  version: number;
  writable: boolean;
  allow_company?: boolean;
  /** @deprecated Compatibility with older native clients. */
  allow_h3c?: boolean;
}
export interface DeviceSelection {
  mode: "local" | "auto";
  device_id: string;
  grant_id: string;
  grant_version: number;
}
