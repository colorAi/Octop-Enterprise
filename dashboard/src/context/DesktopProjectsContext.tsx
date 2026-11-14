import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  PROJECTS_CHANGED,
  setDesktopProjectSnapshot,
  type DesktopProjectState,
  type ProjectFileScope,
} from "../api/desktopProjects";
import { useDesktopWorkspace } from "./DesktopWorkspaceContext";

interface ProjectsContext {
  state: DesktopProjectState | null;
  available: boolean;
  busy: boolean;
  error: string;
  revision: number;
  refresh(): void;
  sync(project_id: string): Promise<DesktopProjectState | null>;
  relocate(project_id: string): Promise<DesktopProjectState | null>;
  update(params: {
    project_id: string;
    name?: string;
    hide?: boolean;
    revoke?: boolean;
  }): Promise<DesktopProjectState | null>;
  choose(): Promise<DesktopProjectState | null>;
  select(
    params: ProjectFileScope & { agent_id?: string },
  ): Promise<DesktopProjectState | null>;
}
const Context = createContext<ProjectsContext>({
  state: null,
  available: false,
  busy: false,
  error: "",
  revision: 0,
  refresh: () => {},
  sync: async () => null,
  relocate: async () => null,
  update: async () => null,
  choose: async () => null,
  select: async () => null,
});
export function DesktopProjectsProvider({ children }: { children: ReactNode }) {
  const workspace = useDesktopWorkspace();
  const [state, setState] = useState<DesktopProjectState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  const owner = workspace.state?.owner_user_id;
  const online = workspace.state?.state === "online";
  const available = Boolean(window.__octopRuntime?.projects);
  const apply = useCallback((next: DesktopProjectState | null) => {
    setDesktopProjectSnapshot(next);
    setState(next);
  }, []);
  const refresh = useCallback(() => setRevision((r) => r + 1), []);
  useEffect(() => {
    window.addEventListener(PROJECTS_CHANGED, refresh);
    return () => window.removeEventListener(PROJECTS_CHANGED, refresh);
  }, [refresh]);
  const invalidate = useCallback(() => {
    sequence.current++;
  }, []);
  useEffect(() => {
    apply(null);
    return () => {
      invalidate();
      setDesktopProjectSnapshot(null);
    };
  }, [owner, online, apply, invalidate]);
  useEffect(() => {
    const bridge = window.__octopRuntime?.projects;
    if (!bridge || !online || !owner) return;
    const request = ++sequence.current;
    let cancelled = false;
    setBusy(true);
    void bridge
      .status()
      .then((next) => {
        if (!cancelled && sequence.current === request) {
          apply(next);
          setError("");
        }
      })
      .catch((err: unknown) => {
        if (!cancelled && sequence.current === request)
          setError(err instanceof Error ? err.message : "无法读取本机项目");
      })
      .finally(() => {
        if (!cancelled && sequence.current === request) setBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [online, owner, available, revision, apply]);
  const change = useCallback(
    async (
      action: "choose" | "select" | "update" | "relocate" | "sync",
      params: ProjectFileScope & {
        agent_id?: string;
        name?: string;
        hide?: boolean;
        revoke?: boolean;
      } = {},
    ) => {
      const bridge = window.__octopRuntime?.projects;
      if (!bridge || !online) return null;
      if (action === "sync" && !bridge.sync) return null;
      const request = ++sequence.current;
      setBusy(true);
      setError("");
      try {
        const next =
          action === "choose"
            ? await bridge.choose()
            : action === "relocate"
            ? await bridge.relocate({ project_id: params.project_id! })
            : action === "update"
            ? await bridge.update({ ...params, project_id: params.project_id! })
            : action === "sync"
            ? (await bridge.sync!({ project_ids: [params.project_id!] })).state
            : await bridge.select(params);
        if (request !== sequence.current) return null;
        apply(next);
        if (action === "sync") refresh();
        return next;
      } catch (err) {
        if (request === sequence.current)
          setError(err instanceof Error ? err.message : "无法选择项目");
        return null;
      } finally {
        if (request === sequence.current) setBusy(false);
      }
    },
    [online, apply, refresh],
  );
  return (
    <Context.Provider
      value={{
        state,
        available,
        busy,
        error,
        revision,
        refresh,
        sync: useCallback(
          (project_id) => change("sync", { project_id }),
          [change],
        ),
        relocate: useCallback(
          (project_id) => change("relocate", { project_id }),
          [change],
        ),
        update: useCallback((params) => change("update", params), [change]),
        choose: useCallback(() => change("choose"), [change]),
        select: useCallback((params) => change("select", params), [change]),
      }}
    >
      {children}
    </Context.Provider>
  );
}
export const useDesktopProjects = () => useContext(Context);
