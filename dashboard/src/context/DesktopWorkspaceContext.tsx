import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { devicesApi } from "../api/modules/devices";
import {
  DESKTOP_WORKSPACE_READY,
  type DesktopWorkspaceState,
} from "../api/desktopWorkspace";
import { useCurrentUser } from "../hooks/useCurrentUser";

interface WorkspaceContext {
  available: boolean;
  enabled: boolean | null;
  state: DesktopWorkspaceState | null;
  busy: boolean;
  error: string;
  choose: () => Promise<void>;
  clear: () => Promise<void>;
  retry: () => void;
}

const Context = createContext<WorkspaceContext>({
  available: false,
  enabled: null,
  state: null,
  busy: false,
  error: "",
  choose: async () => {},
  clear: async () => {},
  retry: () => {},
});

export function DesktopWorkspaceProvider({
  children,
}: {
  children: ReactNode;
}) {
  const user = useCurrentUser();
  const owner = user?.id;
  const [available, setAvailable] = useState(
    Boolean(window.octopDesktopWorkspace),
  );
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [state, setState] = useState<DesktopWorkspaceState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const working = useRef(false);

  useEffect(() => {
    const ready = () => setAvailable(true);
    window.addEventListener(DESKTOP_WORKSPACE_READY, ready);
    return () => window.removeEventListener(DESKTOP_WORKSPACE_READY, ready);
  }, []);

  useEffect(() => {
    const bridge = window.octopDesktopWorkspace;
    if (!available || !bridge || !owner) return;
    let cancelled = false;
    let attempted = false;
    const refresh = async () => {
      if (working.current) return;
      working.current = true;
      try {
        let next = await bridge.status();
        if (cancelled) return;
        if (next.owner_user_id && next.owner_user_id !== owner) {
          next = await bridge.disconnect();
          if (cancelled) return;
        }
        if (!attempted) {
          attempted = true;
          const devices = await devicesApi.list();
          if (cancelled) return;
          setEnabled(devices.enabled);
          if (
            devices.enabled &&
            ["unpaired", "disconnected"].includes(next.state)
          ) {
            setBusy(true);
            const pairing = await devicesApi.pair();
            if (cancelled) return;
            next = await bridge.pair(pairing.code);
          }
        }
        if (cancelled) return;
        if (next.owner_user_id && next.owner_user_id !== owner) {
          await bridge.disconnect();
          throw new Error("session_changed");
        }
        setState(next);
        setError(
          (previous) =>
            next.error ??
            (next.state === "online" && previous === "connection_failed"
              ? ""
              : previous),
        );
      } catch {
        if (!cancelled) setError("connection_failed");
      } finally {
        working.current = false;
        if (!cancelled) setBusy(false);
      }
    };
    setState(null);
    setError("");
    void refresh();
    const timer = window.setInterval(() => void refresh(), 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [available, owner, revision]);

  const change = useCallback(
    async (action: "choose" | "clear") => {
      const bridge = window.octopDesktopWorkspace;
      if (!bridge || working.current) return;
      working.current = true;
      setBusy(true);
      setError("");
      try {
        const next = await bridge[action]();
        if (next.owner_user_id !== owner) throw new Error("session_changed");
        setState(next);
        setError(next.error ?? "");
      } catch (err) {
        const code = err instanceof Error ? err.message : "save_failed";
        setError(
          [
            "directory_unavailable",
            "save_failed",
            "device_offline",
            "session_changed",
          ].includes(code)
            ? code
            : "connection_failed",
        );
      } finally {
        working.current = false;
        setBusy(false);
      }
    },
    [owner],
  );

  return (
    <Context.Provider
      value={{
        available,
        enabled,
        state,
        busy,
        error,
        choose: () => change("choose"),
        clear: () => change("clear"),
        retry: () => setRevision((r) => r + 1),
      }}
    >
      {children}
    </Context.Provider>
  );
}

export function useDesktopWorkspace() {
  return useContext(Context);
}
