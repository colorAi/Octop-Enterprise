import { useEffect, useState } from "react";
import type { DeviceSelection } from "@/api/modules/devices";
import { useDesktopWorkspace } from "@/context/DesktopWorkspaceContext";

export function useChatDeviceSelection(
  scope: string | null | undefined,
  unsupported: boolean,
) {
  const desktop = useDesktopWorkspace();
  const [localIntent, setLocalIntent] = useState<{
    scope: string | null | undefined;
  } | null>(null);
  const remembered = localIntent !== null && localIntent.scope === scope;
  const state = desktop.state;
  const grant =
    state?.state === "online" &&
    !state.error &&
    desktop.error !== "connection_failed"
      ? state.grant
      : null;

  // Remember local intent once a work folder is configured. Losing the grant or
  // the connection must block sending, never silently change a task to server.
  useEffect(() => {
    if (!unsupported && desktop.available && !remembered && state?.path) {
      setLocalIntent({ scope });
    }
  }, [
    unsupported,
    desktop.available,
    remembered,
    scope,
    state?.path,
    state?.device_id,
  ]);

  const device: DeviceSelection | null = unsupported
    ? null
    : desktop.available && (remembered || state?.path)
    ? {
        mode: "auto",
        device_id: state?.device_id ?? "",
        grant_id: grant?.id ?? "",
        grant_version: grant?.version ?? 0,
      }
    : null;
  const initializing =
    desktop.available && !state && desktop.enabled !== false && !desktop.error;
  // The company runtime routes locally regardless of the former tool-location preference.
  return {
    device: window.__octopRuntime ? null : device,
    initializing,
  };
}
