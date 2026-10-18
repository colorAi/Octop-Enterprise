export const CLIENT_CONFIG_CHECK_EVENT = "octop:client-config-check";
export const BEFORE_CLIENT_CONFIG_REFRESH_EVENT =
  "octop:before-client-config-refresh";

export function permissionsSignature(
  role: string,
  permissions: string[] = [],
): string {
  return JSON.stringify([role, [...new Set(permissions)].sort()]);
}

export function prepareClientConfigRefresh(): boolean {
  return window.dispatchEvent(
    new Event(BEFORE_CLIENT_CONFIG_REFRESH_EVENT, { cancelable: true }),
  );
}

export function reloadClientConfig(): void {
  window.location.reload();
}
