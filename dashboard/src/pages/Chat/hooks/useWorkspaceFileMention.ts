import { useDesktopProjects } from "../../../context/DesktopProjectsContext";
import { rankWorkspaceMentionFiles } from "../utils/fileMention";
import { useEffect, useState } from "react";
import {
  searchWorkspaceMentionFiles,
  shouldSearchWorkspaceMentions,
  type WorkspaceMentionFile,
} from "../utils/fileMention";

function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (err instanceof Error && err.name === "AbortError")
  );
}

export function useWorkspaceFileMention(
  agentId: string | null | undefined,
  mentionMenuOpen: boolean,
  mentionQuery: string,
  threadId?: string | null,
): {
  files: WorkspaceMentionFile[];
  loading: boolean;
  error: "need_agent" | "failed" | null;
} {
  const projects = useDesktopProjects();
  const projectId = threadId
    ? projects.state?.threads?.[threadId]
    : projects.state?.current_project_id;
  const local = projects.available && projects.state?.supported !== false;
  const [files, setFiles] = useState<WorkspaceMentionFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<"need_agent" | "failed" | null>(null);

  useEffect(() => {
    if (!mentionMenuOpen) {
      setFiles([]);
      setLoading(false);
      setError(null);
      return;
    }
    if (!shouldSearchWorkspaceMentions(mentionQuery)) {
      setFiles([]);
      setLoading(false);
      setError(null);
      return;
    }
    if (!agentId) {
      setFiles([]);
      setLoading(false);
      setError("need_agent");
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setFiles([]);
      setLoading(true);
      setError(null);
      const search =
        local && window.__octopRuntime?.projects
          ? window.__octopRuntime.projects
              .files({
                project_id: projectId,
                thread_id: threadId || undefined,
                query: mentionQuery,
              })
              .then((result) =>
                rankWorkspaceMentionFiles(result.items, mentionQuery),
              )
          : searchWorkspaceMentionFiles(
              agentId,
              mentionQuery,
              controller.signal,
            );
      void search
        .then((hits) => {
          if (controller.signal.aborted) return;
          setFiles(hits);
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted || isAbortError(err)) return;
          setFiles([]);
          setError("failed");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 250);

    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [mentionMenuOpen, mentionQuery, agentId, threadId, projectId, local]);

  return { files, loading, error };
}
