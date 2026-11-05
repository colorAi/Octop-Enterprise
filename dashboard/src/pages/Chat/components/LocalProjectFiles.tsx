import { useEffect, useState } from "react";
import { Alert, Button, Empty, Input, Spin, Typography } from "antd";
import { Folder, File, ArrowLeft, RefreshCw } from "lucide-react";
import { useDesktopProjects } from "../../../context/DesktopProjectsContext";
import type { ProjectFile } from "../../../api/desktopProjects";
import type { LocalArtifact } from "../../../api/desktopRuntime";
import styles from "./ProjectNavigation.module.less";

export default function LocalProjectFiles({
  threadId,
  filePath,
}: {
  threadId?: string | null;
  filePath?: string;
}) {
  const projects = useDesktopProjects();
  const projectId = threadId
    ? projects.state?.threads?.[threadId]
    : projects.state?.current_project_id;
  const [path, setPath] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(filePath || "");
  const [files, setFiles] = useState<ProjectFile[]>([]);
  const [content, setContent] = useState<
    (LocalArtifact & { text?: string }) | null
  >(null);
  const [image, setImage] = useState("");
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    setPath("");
    setQuery("");
    setSelected(filePath || "");
  }, [projectId, threadId, filePath]);
  useEffect(() => {
    let cancelled = false;
    setContent(null);
    setImage("");
    setFiles([]);
    setError("");
    const bridge = window.__octopRuntime?.projects;
    if (!bridge || !projectId) {
      setLoading(false);
      return;
    }
    const scope = { project_id: projectId, thread_id: threadId || undefined };
    setLoading(true);
    const timer = window.setTimeout(
      () => {
        void (async () => {
          if (selected) {
            const result = await bridge.read({ ...scope, path: selected });
            if (cancelled) return;
            setContent(result);
            if (
              /\.(png|jpe?g|webp|gif)$/i.test(result.path) &&
              window.__octopRuntime?.previewImage
            ) {
              const preview = await window.__octopRuntime.previewImage(result);
              if (!cancelled) setImage(preview.data_url);
            }
          } else {
            const result = await bridge.files({ ...scope, path, query });
            if (!cancelled) {
              setFiles(result.items);
              setTruncated(result.truncated);
            }
          }
        })()
          .catch((err: unknown) => {
            if (!cancelled)
              setError(err instanceof Error ? err.message : "读取项目文件失败");
          })
          .finally(() => {
            if (!cancelled) setLoading(false);
          });
      },
      query ? 250 : 0,
    );
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [projectId, threadId, path, query, selected, revision]);
  if (!projectId)
    return (
      <div className={styles.files}>
        <Alert
          type="info"
          message={
            threadId
              ? "此会话没有本机项目记录，无法打开本机文件。"
              : "请先选择项目文件夹。"
          }
        />
      </div>
    );
  return (
    <div className={styles.files}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {(selected || path) && (
          <Button
            size="small"
            aria-label="返回项目目录"
            icon={<ArrowLeft size={14} />}
            onClick={() => {
              if (selected) setSelected("");
              else setPath(path.split("/").slice(0, -1).join("/"));
            }}
          />
        )}
        <Typography.Text ellipsis title={selected || path}>
          {selected || path || projects.state?.projects[projectId]?.name}
        </Typography.Text>
        <Button
          size="small"
          aria-label="刷新项目文件"
          icon={<RefreshCw size={14} />}
          onClick={() => setRevision((r) => r + 1)}
        />
      </div>
      {!selected && (
        <Input.Search
          aria-label="搜索项目文件"
          placeholder="搜索项目文件"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      )}
      {loading && <Spin />}
      {error && <Alert type="warning" message={error} />}
      {content && (
        <>
          <Button
            onClick={() => {
              void window
                .__octopRuntime!.reveal(content)
                .catch((err: unknown) => setError(String(err)));
            }}
          >
            在本地文件夹中显示
          </Button>
          {image ? (
            <img alt={selected.split("/").pop()} src={image} />
          ) : content.text !== undefined ? (
            <pre>{content.text}</pre>
          ) : (
            !loading && (
              <Typography.Text type="secondary">
                此类型请在本地文件夹中查看。
              </Typography.Text>
            )
          )}
        </>
      )}
      {!selected && !loading && !error && files.length === 0 && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="没有匹配的文件"
        />
      )}
      {!selected &&
        files.map((file) => (
          <Button
            type="text"
            key={file.path}
            style={{ justifyContent: "flex-start", flexShrink: 0 }}
            icon={file.is_dir ? <Folder size={15} /> : <File size={15} />}
            title={file.path}
            onClick={() => {
              if (file.is_dir) {
                setPath(file.path);
                setQuery("");
              } else setSelected(file.path);
            }}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
              {query ? file.path : file.name}
            </span>
          </Button>
        ))}
      {!selected && truncated && (
        <Typography.Text type="secondary">
          结果较多，请进入子目录或缩小搜索范围。
        </Typography.Text>
      )}
    </div>
  );
}
