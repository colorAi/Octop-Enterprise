import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Dropdown,
  Empty,
  Input,
  Modal,
  Select,
  Spin,
  Typography,
} from "antd";
import { FolderOpen, Plus, RefreshCw } from "lucide-react";
import type { OctopAgent } from "../../../context/AgentContext";
import { useDesktopProjects } from "../../../context/DesktopProjectsContext";
import {
  getDesktopProjectSnapshot,
  type DesktopProjectState,
  type ProjectThread,
} from "../../../api/desktopProjects";
import { onSessionEvent, onStreamEvent } from "../hooks/chatStore";
import styles from "./ProjectNavigation.module.less";

type Props = {
  agents: OctopAgent[];
  agentId?: string | null;
  threadId?: string | null;
  onNewChat: (agentId: string) => void;
  onSelect: (threadId: string, agentId: string) => void;
  children: ReactNode;
};
export function ProjectNavigation({
  agents,
  agentId,
  threadId,
  onNewChat,
  onSelect,
  children,
}: Props) {
  const projects = useDesktopProjects();
  const [rename, setRename] = useState<string | null>(null);
  const [threads, setThreads] = useState<ProjectThread[]>([]);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const sequence = useRef(0);
  const state = projects.state;
  const current = state?.current_project_id;
  const project = current ? state?.projects?.[current] : null;
  const experts = useMemo(
    () => agents.filter((a) => !a.history_only && a.kind !== "team"),
    [agents],
  );
  const expert =
    experts.find((a) => a.agent_id === (agentId || project?.last_expert))
      ?.agent_id || experts[0]?.agent_id;
  const start = (next: DesktopProjectState | null) => {
    if (!next || next.cancelled || !next.current_project_id) return;
    const remembered = next.projects[next.current_project_id]?.last_expert;
    const selected =
      experts.find((a) => a.agent_id === remembered)?.agent_id || expert;
    if (selected) onNewChat(selected);
  };
  const openProject = async (next: DesktopProjectState | null) => {
    if (!next || next.cancelled || !next.current_project_id) return;
    const p = next.projects[next.current_project_id];
    if (p?.last_thread) {
      try {
        const page = await window.__octopRuntime!.projects!.threads({
          project_id: p.id,
        });
        const last =
          page.items.find((t) => t.thread_id === p.last_thread) ||
          page.items[0];
        if (getDesktopProjectSnapshot()?.current_project_id !== p.id) return;
        if (last) {
          onSelect(last.thread_id, last.agent_id);
          return;
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "无法读取项目会话");
      }
    }
    if (
      getDesktopProjectSnapshot()?.current_project_id ===
      next.current_project_id
    )
      start(next);
  };
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
      initialActions.current.onNewChat(p.last_expert);
  }, [current, threadId, state, experts]);
  useEffect(() => {
    const a = onSessionEvent(() => setRevision((r) => r + 1));
    const b = onStreamEvent((e) => {
      if (e.kind === "streamEnd") setRevision((r) => r + 1);
      else
        setThreads((rows) =>
          rows.map((row) =>
            row.thread_id === e.sessionId
              ? { ...row, task_status: "running" }
              : row,
          ),
        );
    });
    return () => {
      a();
      b();
    };
  }, []);
  useEffect(() => {
    const request = ++sequence.current;
    let cancelled = false;
    setThreads([]);
    setMore(false);
    setError("");
    if (!current || !state?.supported) return;
    setLoading(true);
    void window
      .__octopRuntime!.projects!.threads({ project_id: current })
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
    };
  }, [current, state?.supported, revision, projects.revision, threadId]);
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
  return (
    <div className={styles.navigation}>
      <div className={styles.heading}>
        <FolderOpen size={16} />
        <strong>项目</strong>
        <Button
          type="text"
          size="small"
          aria-label="刷新项目"
          onClick={projects.refresh}
          icon={<RefreshCw size={14} />}
        />
      </div>
      <Select
        aria-label="当前项目"
        placeholder="选择项目文件夹"
        value={current || undefined}
        disabled={projects.busy}
        style={{ width: "100%" }}
        options={Object.values(state?.projects ?? {})
          .filter((p) => !p.hidden)
          .map((p) => ({ value: p.id, label: p.name, title: p.path }))}
        onChange={(id) => {
          void projects.select({ project_id: id }).then(openProject);
        }}
      />
      <Button
        block
        icon={<Plus size={14} />}
        loading={projects.busy}
        onClick={() => void projects.choose().then(start)}
      >
        添加项目文件夹
      </Button>
      {project && (
        <>
          <Typography.Text
            type="secondary"
            className={styles.path}
            title={project.path}
          >
            {project.path}
          </Typography.Text>
          <Dropdown
            menu={{
              items: [
                { key: "rename", label: "重命名项目" },
                { key: "relocate", label: "重新定位原文件夹" },
                { key: "hide", label: "从项目列表移除" },
                { key: "revoke", label: "撤销本机授权" },
              ],
              onClick: ({ key }) => {
                if (key === "rename") {
                  setRename(project.name);
                  return;
                }
                if (key === "relocate") {
                  void projects.relocate(project.id);
                  return;
                }
                void projects
                  .update({
                    project_id: project.id,
                    hide: key === "hide",
                    revoke: key === "revoke",
                  })
                  .then((next) => {
                    if (next && key === "hide" && expert) onNewChat(expert);
                  });
              },
            }}
          >
            <Button size="small" disabled={projects.busy}>
              管理项目
            </Button>
          </Dropdown>
          <Modal
            title="重命名项目"
            open={rename !== null}
            onCancel={() => setRename(null)}
            confirmLoading={projects.busy}
            okButtonProps={{ disabled: !rename?.trim() }}
            onOk={() => {
              void projects
                .update({ project_id: project.id, name: rename?.trim() })
                .then((next) => {
                  if (next) setRename(null);
                });
            }}
          >
            <Input
              aria-label="项目名称"
              maxLength={200}
              value={rename || ""}
              onChange={(e) => setRename(e.target.value)}
            />
          </Modal>
        </>
      )}
      {project && !project.authorized && (
        <Alert type="warning" message="请选择原文件夹重新授权" />
      )}
      {projects.error && <Alert type="warning" message={projects.error} />}
      <div className={styles.expertRow}>
        <Select
          aria-label="当前专家"
          value={expert}
          placeholder="选择专家"
          style={{ flex: 1, minWidth: 0 }}
          options={experts.map((a) => ({
            value: a.agent_id,
            label: a.name || a.agent_id,
          }))}
          disabled={!current || projects.busy}
          onChange={(id) => {
            void projects
              .select({ project_id: current, agent_id: id })
              .then((next) => {
                if (next) onNewChat(id);
              });
          }}
        />
        <Button
          title="在当前项目新建对话"
          aria-label="在当前项目新建对话"
          icon={<Plus size={16} />}
          disabled={!current || !expert || projects.busy}
          onClick={() => {
            if (expert) onNewChat(expert);
          }}
        />
      </div>
      <div className={styles.conversations}>
        {loading && threads.length === 0 ? <Spin size="small" /> : null}
        {error ? (
          <Alert
            type="warning"
            message={error}
            action={
              <Button size="small" onClick={() => setRevision((r) => r + 1)}>
                重试
              </Button>
            }
          />
        ) : null}
        {!loading && !error && threads.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={project ? "在这个项目开始新对话" : "选择文件夹开始"}
          />
        ) : null}
        {threads.map((thread) => (
          <button
            className={`${styles.thread} ${
              thread.thread_id === threadId ? styles.active : ""
            }`}
            key={thread.thread_id}
            onClick={() => {
              void projects
                .select({
                  project_id: thread.project_id,
                  thread_id: thread.thread_id,
                  agent_id: thread.agent_id,
                })
                .then((next) => {
                  if (next) onSelect(thread.thread_id, thread.agent_id);
                });
            }}
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
            block
            size="small"
            loading={loading}
            onClick={() => {
              if (!current) return;
              const request = sequence.current;
              setLoading(true);
              void window
                .__octopRuntime!.projects!.threads({
                  project_id: current,
                  offset: threads.length,
                })
                .then((result) => {
                  if (request === sequence.current) {
                    setThreads((old) => [
                      ...old,
                      ...result.items.filter(
                        (t) => !old.some((o) => o.thread_id === t.thread_id),
                      ),
                    ]);
                    setMore(result.has_more);
                  }
                })
                .catch((err: unknown) => {
                  if (request === sequence.current) setError(String(err));
                })
                .finally(() => {
                  if (request === sequence.current) setLoading(false);
                });
            }}
          >
            更多会话
          </Button>
        )}
      </div>
      <details className={styles.history}>
        <summary>专家与历史会话</summary>
        {children}
      </details>
    </div>
  );
}

export function ProjectBar({
  threadId,
  onChoose,
}: {
  threadId: string | null;
  onChoose: () => void;
}) {
  const projects = useDesktopProjects();
  const state = projects.state;
  const id = threadId ? state?.threads?.[threadId] : state?.current_project_id;
  const project = id ? state?.projects?.[id] : null;
  const select = projects.select;
  useEffect(() => {
    if (threadId && id) void select({ project_id: id, thread_id: threadId });
  }, [threadId, id, select]);
  if (!projects.available || state?.supported === false) return null;
  return (
    <div className={styles.bar}>
      <FolderOpen size={15} />
      <span title={project?.path}>
        {project
          ? `${project.name} · ${project.path}`
          : threadId
          ? "此会话未关联本机项目"
          : "先选择一个项目文件夹"}
      </span>
      <Button size="small" disabled={projects.busy} onClick={onChoose}>
        {threadId ? "新建项目对话" : "选择文件夹"}
      </Button>
    </div>
  );
}
