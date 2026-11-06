import { useEffect } from "react";
import { Dropdown, type MenuProps } from "antd";
import { Check, ChevronDown, FolderOpen, Plus } from "lucide-react";
import type { DesktopProject } from "../../../api/desktopProjects";
import { useDesktopProjects } from "../../../context/DesktopProjectsContext";
import styles from "./ComposerProjectPicker.module.less";

function folderName(project: DesktopProject) {
  return (
    project.path
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .pop() || project.name
  );
}

export default function ComposerProjectPicker({
  threadId,
  agentId,
  onProjectChange,
}: {
  threadId: string | null;
  agentId?: string | null;
  onProjectChange: () => void;
}) {
  const projects = useDesktopProjects();
  const state = projects.state;
  const id = threadId ? state?.threads[threadId] : state?.current_project_id;
  const project = id ? state?.projects[id] : undefined;
  const select = projects.select;
  useEffect(() => {
    if (threadId && id) void select({ project_id: id, thread_id: threadId });
  }, [threadId, id, select]);
  if (!projects.available || state?.supported === false) return null;

  const chooseProject = async (projectId?: string) => {
    if (projectId && projectId === id) return;
    const next = projectId
      ? await select({
          project_id: projectId,
          ...(agentId ? { agent_id: agentId } : {}),
        })
      : await projects.choose();
    if (
      !next ||
      next.cancelled ||
      !next.current_project_id ||
      next.current_project_id === id
    )
      return;
    // Never rebind an existing conversation: navigate to a fresh draft instead.
    onProjectChange();
  };
  const items: MenuProps["items"] = [
    ...(threadId
      ? [
          {
            key: "hint",
            disabled: true,
            label: (
              <span className={styles.hint}>
                选择其他文件夹将在该项目新建对话
              </span>
            ),
          },
        ]
      : []),
    ...Object.values(state?.projects ?? {})
      .filter((p) => !p.hidden)
      .map((p) => ({
        key: `project:${p.id}`,
        disabled: !p.authorized || p.id === id,
        icon: p.id === id ? <Check size={14} /> : <FolderOpen size={14} />,
        label: (
          <span className={styles.option} title={p.path}>
            {folderName(p)}
            {!p.authorized ? " · 需授权" : ""}
          </span>
        ),
        onClick: () => void chooseProject(p.id),
      })),
    { type: "divider" },
    {
      key: "choose",
      icon: <Plus size={14} />,
      label: "选择其他文件夹…",
      onClick: () => void chooseProject(),
    },
    ...(project && !project.authorized
      ? [
          {
            key: "relocate",
            label: "重新定位原文件夹并授权",
            onClick: () => void projects.relocate(project.id),
          },
        ]
      : []),
  ];
  const label = project
    ? folderName(project)
    : threadId
    ? "未关联项目"
    : "选择文件夹";
  return (
    <Dropdown
      trigger={["click"]}
      placement="topLeft"
      menu={{
        items,
        style: {
          maxHeight: "min(420px, calc(100vh - 80px))",
          overflowY: "auto",
        },
      }}
      disabled={projects.busy || !state}
    >
      <button
        type="button"
        className={styles.picker}
        disabled={projects.busy || !state}
        aria-label={`项目文件夹：${label}`}
        title={project?.path || label}
      >
        <FolderOpen size={15} />
        <span>{label}</span>
        <ChevronDown size={12} />
      </button>
    </Dropdown>
  );
}
