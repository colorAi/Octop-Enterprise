import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Alert, Button, Dropdown, Input, Modal, Spin } from "antd";
import {
  ChevronRight,
  Folder,
  FolderOpen,
  MoreHorizontal,
  Plus,
  RefreshCw,
} from "lucide-react";
import type { OctopAgent } from "../../../context/AgentContext";
import { useDesktopProjects } from "../../../context/DesktopProjectsContext";
import type {
  DesktopProject,
  ProjectThread,
} from "../../../api/desktopProjects";
import { onSessionEvent, onStreamEvent } from "../hooks/chatStore";
import styles from "./ProjectNavigation.module.less";

type Props = {
  agents: OctopAgent[];
  agentId?: string | null;
  threadId?: string | null;
  onNewChat: (agentId: string, projectId?: string) => void;
  onSelect: (threadId: string, agentId: string) => void;
  children: ReactNode;
};

/** Each expanded folder owns its query and pagination; browsing never selects it. */
function ProjectFolder({
  project,
  agents,
  experts,
  expanded,
  current,
  threadId,
  revision,
  onToggle,
  onNewChat,
  onSelect,
  onManage,
}: {
  project: DesktopProject;
  agents: OctopAgent[];
  experts: OctopAgent[];
  expanded: boolean;
  current: boolean;
  threadId?: string | null;
  revision: number;
  onToggle(): void;
  onNewChat(agentId: string): void;
  onSelect(thread: ProjectThread): void;
  onManage(key: string): void;
}) {
  const { busy } = useDesktopProjects();
  const [threads, setThreads] = useState<ProjectThread[]>([]);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const sequence = useRef(0);
  const invalidate = useCallback(() => {
    sequence.current++;
  }, []);
  const pagination = useRef(false);
  const projectId = project.id;
  useEffect(() => {
    const request = ++sequence.current;
    let cancelled = false;
    pagination.current = false;
    if (!expanded) return;
    setLoading(true);
    setError("");
    void window
      .__octopRuntime!.projects!.threads({ project_id: projectId })
      .then((result) => {
        if (cancelled || request !== sequence.current) return;
        setThreads(result.items);
        setMore(result.has_more);
      })
      .catch((err: unknown) => {
        if (!cancelled && request === sequence.current)
          setError(err instanceof Error ? err.message : "无法读取项目会话");
      })
      .finally(() => {
        if (!cancelled && request === sequence.current) setLoading(false);
      });
    return () => {
      cancelled = true;
      invalidate();
    };
  }, [expanded, projectId, revision, retry, invalidate]);
  useEffect(
    () =>
      onStreamEvent((event) => {
        if (event.kind !== "streamEnd")
          setThreads((rows) =>
            rows.map((row) =>
              row.thread_id === event.sessionId
                ? { ...row, task_status: "running" }
                : row,
            ),
          );
      }),
    [],
  );
  const loadMore = async () => {
    if (loading || pagination.current) return;
    pagination.current = true;
    const request = sequence.current;
    setLoading(true);
    setError("");
    try {
      const result = await window.__octopRuntime!.projects!.threads({
        project_id: projectId,
        offset: threads.length,
      });
      if (request !== sequence.current) return;
      setThreads((old) => [
        ...old,
        ...result.items.filter(
          (t) => !old.some((o) => o.thread_id === t.thread_id),
        ),
      ]);
      setMore(result.has_more);
    } catch (err) {
      if (request === sequence.current)
        setError(err instanceof Error ? err.message : "无法读取项目会话");
    } finally {
      if (request === sequence.current) {
        pagination.current = false;
        setLoading(false);
      }
    }
  };
  const expertMenu = {
    items: experts.map((agent) => ({
      key: agent.agent_id,
      label: `${agent.name || agent.agent_id}${
        agent.agent_id === project.last_expert ? " · 最近使用" : ""
      }`,
    })),
    onClick: ({ key }: { key: string }) => onNewChat(key),
  };
  const unavailable = busy || !project.authorized || experts.length === 0;
  return (
    <section className={styles.project} aria-label={`${project.name}项目`}>
      <div
        className={`${styles.folderRow} ${current ? styles.currentFolder : ""}`}
      >
        <button
          className={styles.folderToggle}
          aria-label={`${expanded ? "折叠" : "展开"}项目 ${project.name}`}
          aria-expanded={expanded}
          aria-controls={`project-threads-${project.id}`}
          onClick={onToggle}
          title={project.path}
        >
          <ChevronRight
            size={14}
            className={expanded ? styles.expandedChevron : undefined}
          />
          {expanded ? <FolderOpen size={16} /> : <Folder size={16} />}
          <span>{project.name}</span>
        </button>
        <div className={styles.folderActions}>
          <Dropdown
            trigger={["click"]}
            menu={expertMenu}
            disabled={unavailable}
          >
            <Button
              type="text"
              size="small"
              icon={<Plus size={14} />}
              aria-label={`在 ${project.name} 新建对话`}
              title="选择专家，新建对话"
              disabled={unavailable}
            />
          </Dropdown>
          <Dropdown
            trigger={["click"]}
            menu={{
              items: [
                { key: "rename", label: "重命名项目" },
                { key: "relocate", label: "重新定位原文件夹" },
                { key: "hide", label: "从项目列表移除" },
                { key: "revoke", label: "撤销本机授权" },
              ],
              onClick: ({ key }) => onManage(key),
            }}
          >
            <Button
              type="text"
              size="small"
              icon={<MoreHorizontal size={15} />}
              aria-label={`管理项目 ${project.name}`}
              disabled={busy}
            />
          </Dropdown>
        </div>
      </div>
      {expanded && (
        <div
          id={`project-threads-${project.id}`}
          className={styles.conversations}
        >
          {!project.authorized && (
            <p className={styles.folderHint}>
              请通过项目菜单重新定位原文件夹并授权
            </p>
          )}
          {loading && threads.length === 0 && (
            <div className={styles.folderHint}>
              <Spin size="small" />
            </div>
          )}
          {error && (
            <div className={styles.folderError} role="alert">
              <span>{error}</span>
              <Button
                type="text"
                size="small"
                onClick={() => setRetry((r) => r + 1)}
              >
                重试
              </Button>
            </div>
          )}
          {!loading && !error && threads.length === 0 && (
            <div className={styles.folderHint}>
              <span>还没有对话</span>
              <Dropdown
                trigger={["click"]}
                menu={expertMenu}
                disabled={unavailable}
              >
                <Button type="link" size="small" disabled={unavailable}>
                  新建对话
                </Button>
              </Dropdown>
            </div>
          )}
          {threads.map((thread) => (
            <button
              className={`${styles.thread} ${
                thread.thread_id === threadId ? styles.active : ""
              }`}
              key={thread.thread_id}
              onClick={() => onSelect(thread)}
              disabled={busy}
              aria-current={thread.thread_id === threadId ? "page" : undefined}
            >
              <span>
                {thread.pinned ? "★ " : ""}
                {thread.title || "新对话"}
              </span>
              <small>
                {agents.find((a) => a.agent_id === thread.agent_id)?.name ||
                  "历史专家"}
                {thread.task_status === "running"
                  ? " · 执行中"
                  : thread.task_status === "unknown"
                  ? " · 待核对"
                  : thread.task_status === "cancel_requested"
                  ? " · 取消中"
                  : ""}
              </small>
            </button>
          ))}
          {more && (
            <Button
              type="text"
              size="small"
              className={styles.loadMore}
              loading={loading}
              onClick={() => void loadMore()}
            >
              更多对话
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

export function ProjectNavigation({
  agents,
  agentId,
  threadId,
  onNewChat,
  onSelect,
  children,
}: Props) {
  const projects = useDesktopProjects();
  const state = projects.state;
  const current = state?.current_project_id;
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [rename, setRename] = useState<{ id: string; name: string } | null>(
    null,
  );
  const [revision, setRevision] = useState(0);
  const experts = useMemo(
    () => agents.filter((a) => !a.history_only && a.kind !== "team"),
    [agents],
  );
  const expertFor = (project: DesktopProject) =>
    experts.find((a) => a.agent_id === project.last_expert)?.agent_id ||
    experts.find((a) => a.agent_id === agentId)?.agent_id ||
    experts[0]?.agent_id;
  const start = async (project: DesktopProject, expertId: string) => {
    const next = await projects.select({
      project_id: project.id,
      agent_id: expertId,
    });
    if (!next) return;
    setExpanded((old) => ({ ...old, [project.id]: true }));
    onNewChat(expertId, project.id);
  };
  useEffect(() => {
    if (current) setExpanded((old) => ({ ...old, [current]: true }));
  }, [current, threadId]);
  // Keep the existing startup restoration; expanding folders alone never navigates.
  const latestSelection = useRef({ current, threadId });
  latestSelection.current = { current, threadId };
  const initialProject = useRef("");
  const initialActions = useRef({ onSelect, onNewChat });
  initialActions.current = { onSelect, onNewChat };
  useEffect(() => {
    if (!current || initialProject.current) return;
    initialProject.current = current;
    if (threadId) return;
    const p = state?.projects[current];
    if (p?.last_thread) {
      void window
        .__octopRuntime!.projects!.threads({ project_id: current })
        .then((page) => {
          const last = page.items.find((t) => t.thread_id === p.last_thread);
          if (
            last &&
            latestSelection.current.current === current &&
            !latestSelection.current.threadId
          )
            initialActions.current.onSelect(last.thread_id, last.agent_id);
        })
        .catch(() => {});
    } else if (
      p?.last_expert &&
      experts.some((a) => a.agent_id === p.last_expert)
    )
      initialActions.current.onNewChat(p.last_expert, current);
  }, [current, threadId, state, experts]);
  useEffect(() => {
    const sessions = onSessionEvent(() => setRevision((r) => r + 1));
    const streams = onStreamEvent((event) => {
      if (event.kind === "streamEnd") setRevision((r) => r + 1);
    });
    return () => {
      sessions();
      streams();
    };
  }, []);
  const manage = (project: DesktopProject, key: string) => {
    if (key === "rename") {
      setRename({ id: project.id, name: project.name });
      return;
    }
    if (key === "relocate") {
      void projects.relocate(project.id);
      return;
    }
    void projects.update({
      project_id: project.id,
      hide: key === "hide",
      revoke: key === "revoke",
    });
  };
  if (!projects.available) return <>{children}</>;
  if (state?.supported === false)
    return (
      <>
        <Alert
          type="info"
          message="当前组件使用单目录模式，更新客户端和公司服务后可使用多项目。"
        />
        {children}
      </>
    );
  const visible = Object.values(state?.projects ?? {}).filter((p) => !p.hidden);
  return (
    <div className={styles.navigation}>
      <div className={styles.heading}>
        <strong>项目</strong>
        <Button
          type="text"
          size="small"
          aria-label="刷新项目"
          onClick={projects.refresh}
          icon={<RefreshCw size={14} />}
        />
      </div>
      <Button
        type="text"
        className={styles.addProject}
        icon={<Plus size={15} />}
        loading={projects.busy}
        onClick={() =>
          void projects.choose().then((next) => {
            if (!next || next.cancelled || !next.current_project_id) return;
            const p = next.projects[next.current_project_id];
            setExpanded((old) => ({ ...old, [p.id]: true }));
            const expert = expertFor(p);
            if (expert) void start(p, expert);
          })
        }
      >
        添加项目文件夹
      </Button>
      {projects.error && <Alert type="warning" message={projects.error} />}
      <div className={styles.projectList}>
        {visible.map((project) => (
          <ProjectFolder
            key={project.id}
            project={project}
            agents={agents}
            experts={experts}
            current={current === project.id}
            expanded={expanded[project.id] ?? current === project.id}
            threadId={threadId}
            revision={revision + projects.revision}
            onToggle={() =>
              setExpanded((old) => ({
                ...old,
                [project.id]: !(old[project.id] ?? current === project.id),
              }))
            }
            onNewChat={(id) => void start(project, id)}
            onSelect={(thread) => {
              void projects
                .select({
                  project_id: project.id,
                  thread_id: thread.thread_id,
                  agent_id: thread.agent_id,
                })
                .then((next) => {
                  if (next) onSelect(thread.thread_id, thread.agent_id);
                });
            }}
            onManage={(key) => manage(project, key)}
          />
        ))}
        {state && visible.length === 0 && (
          <p className={styles.empty}>添加项目文件夹，在文件夹下开始对话</p>
        )}
      </div>
      <Modal
        title="重命名项目"
        open={rename !== null}
        onCancel={() => setRename(null)}
        confirmLoading={projects.busy}
        okButtonProps={{ disabled: !rename?.name.trim() }}
        onOk={() => {
          if (!rename) return;
          void projects
            .update({ project_id: rename.id, name: rename.name.trim() })
            .then((next) => {
              if (next) setRename(null);
            });
        }}
      >
        <Input
          aria-label="项目名称"
          maxLength={200}
          value={rename?.name || ""}
          onChange={(e) =>
            setRename((old) => old && { ...old, name: e.target.value })
          }
        />
      </Modal>
    </div>
  );
}
