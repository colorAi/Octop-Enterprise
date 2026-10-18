import type { DirectoryGrant } from "./modules/devices";

export interface DesktopWorkspaceState {
  state: "unpaired" | "connecting" | "online" | "reconnecting" | "disconnected";
  device_id: string;
  owner_user_id: number;
  path: string;
  grant: DirectoryGrant | null;
  error?: string;
}

export interface DesktopWorkspaceBridge {
  status: () => Promise<DesktopWorkspaceState>;
  pair: (code: string) => Promise<DesktopWorkspaceState>;
  choose: () => Promise<DesktopWorkspaceState>;
  clear: () => Promise<DesktopWorkspaceState>;
  disconnect: () => Promise<DesktopWorkspaceState>;
}

declare global {
  interface Window {
    octopDesktopWorkspace?: DesktopWorkspaceBridge;
  }
}

export const DESKTOP_WORKSPACE_READY = "octop:desktop-workspace-ready";
export const OPEN_WORKSPACE_SETTINGS = "octop:open-workspace-settings";

export function openWorkspaceSettings() {
  window.dispatchEvent(new Event(OPEN_WORKSPACE_SETTINGS));
}
