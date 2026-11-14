import { createRuntimeTaskId, type LocalArtifact } from "./desktopRuntime";

export interface DesktopProject {
  id: string;
  name: string;
  path: string;
  authorized: boolean;
  revision: number;
  legacy?: boolean;
  last_expert?: string;
  last_thread?: string;
  hidden?: boolean;
  sync_pending?: boolean;
}
export interface DesktopProjectState {
  supported: boolean;
  cancelled?: boolean;
  current_project_id: string;
  projects: Record<string, DesktopProject>;
  threads: Record<string, string>;
}
export interface ProjectThread {
  thread_id: string;
  agent_id: string;
  project_id: string;
  title: string | null;
  pinned: boolean;
  task_status?: string | null;
}
export interface ProjectFile {
  path: string;
  name: string;
  is_dir: boolean;
  size: number;
}
export interface ProjectFileScope {
  project_id?: string;
  thread_id?: string;
}
export interface ProjectImportResult {
  cancelled?: boolean;
  path?: string;
  project_ids?: string[];
  synced_count?: number;
  sync_pending?: boolean;
  sync_error?: string;
  state?: DesktopProjectState;
}
export interface DesktopProjectsBridge {
  export?(params: {
    project_ids: string[];
  }): Promise<{ cancelled?: boolean; path?: string }>;
  import?(): Promise<ProjectImportResult>;
  sync?(params: { project_ids: string[] }): Promise<{
    synced_count: number;
    state: DesktopProjectState;
  }>;
  accountProjects?(): Promise<{ project_id: string; name: string }[]>;
  attach?(params: { project_id: string }): Promise<ProjectImportResult>;
  continue?(params: {
    project_id: string;
    source_thread_id: string;
    thread_id: string;
    agent_id: string;
  }): Promise<{ thread_id: string; agent_id: string; copied_messages: number }>;
  status(): Promise<DesktopProjectState>;
  relocate(params: { project_id: string }): Promise<DesktopProjectState>;
  fork(params: {
    source_thread_id: string;
    thread_id: string;
    message_id?: string;
    assistant_turns_from_end?: number;
  }): Promise<{
    thread_id: string;
    agent_id: string;
    project_id: string;
    session_key: string;
    copied_messages: number;
  }>;
  update(params: {
    project_id: string;
    name?: string;
    hide?: boolean;
    revoke?: boolean;
  }): Promise<DesktopProjectState>;
  choose(): Promise<DesktopProjectState>;
  select(
    params: ProjectFileScope & { agent_id?: string },
  ): Promise<DesktopProjectState>;
  create(params: {
    project_id: string;
    agent_id: string;
    thread_id: string;
  }): Promise<{
    thread_id: string;
    agent_id: string;
    project_id: string;
    session_key: string;
  }>;
  threads(params: {
    project_id: string;
    offset?: number;
  }): Promise<{ items: ProjectThread[]; has_more: boolean }>;
  files(
    params: ProjectFileScope & { path?: string; query?: string },
  ): Promise<{ items: ProjectFile[]; truncated: boolean }>;
  read(
    params: ProjectFileScope & { path: string },
  ): Promise<LocalArtifact & { text?: string }>;
}
export const PROJECTS_CHANGED = "octop:projects-changed";
let catalog: DesktopProjectState | null = null;
export function setDesktopProjectSnapshot(state: DesktopProjectState | null) {
  catalog = state;
}
export function getDesktopProjectSnapshot() {
  return catalog;
}
export function notifyProjectsChanged() {
  window.dispatchEvent(new Event(PROJECTS_CHANGED));
}

/** Capture the project before yielding so a sidebar switch cannot retarget a send. */
export async function createDesktopProjectThread(agentId: string) {
  const bridge = window.__octopRuntime?.projects;
  if (!bridge) return null;
  const state = catalog ?? (await bridge.status());
  if (!state.supported) return null;
  const projectId = state.current_project_id;
  if (!projectId) throw new Error("请先选择项目文件夹，再开始对话。");
  const result = await bridge.create({
    project_id: projectId,
    agent_id: agentId,
    thread_id: `thr_${createRuntimeTaskId()}`,
  });
  notifyProjectsChanged();
  return result;
}
