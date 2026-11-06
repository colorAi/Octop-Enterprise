import { useEffect, useRef, useState } from "react";
import { Button, Dropdown } from "antd";
import { useTranslation } from "react-i18next";
import { useAgent } from "../../../context/AgentContext";
import { useDesktopProjects } from "../../../context/DesktopProjectsContext";
import { octopAgentsApi } from "../../../api/modules/octopAgents";
import { octopThreadsApi } from "../../../api/modules/octopThreads";
import { isAgentChatReady } from "../../../utils/agentError";
import styles from "./HistoryContinuation.module.less";

export default function HistoryContinuation({
  agentId,
  threadId,
  recommendedAgentId,
  onContinued,
}: {
  agentId: string;
  threadId: string;
  recommendedAgentId?: string | null;
  onContinued: (agentId: string, threadId: string) => void;
}) {
  const { t } = useTranslation();
  const { agents, refresh } = useAgent();
  const projects = useDesktopProjects();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef(0);
  const pending = useRef(false);
  useEffect(
    () => () => {
      request.current++;
    },
    [],
  );
  const experts = agents.filter(
    (agent) =>
      !agent.history_only &&
      !agent.bridge &&
      agent.kind !== "team" &&
      (agent.is_owner || agent.managed || agent.requires_private_instance) &&
      (agent.requires_private_instance || isAgentChatReady(agent.state, agent)),
  );
  const candidate = recommendedAgentId ?? agentId;
  const target = experts.find((agent) => agent.agent_id === candidate);
  const folders = Object.values(projects.state?.projects ?? {}).filter(
    (project) => !project.hidden && project.authorized,
  );
  const folderId =
    projects.state?.threads[threadId] ?? projects.state?.current_project_id;
  const folder = folders.find((project) => project.id === folderId);

  const resume = async (expert: (typeof experts)[number]) => {
    if (pending.current || projects.busy) return;
    pending.current = true;
    const sequence = ++request.current;
    setBusy(true);
    setError("");
    try {
      let projectId = folder?.id;
      if (projects.available && !projectId) {
        const next = await projects.choose();
        if (request.current !== sequence || !next || next.cancelled) return;
        const selected = next.projects[next.current_project_id];
        if (!selected?.authorized || selected.hidden) return;
        projectId = selected.id;
      }
      const targetId = expert.requires_private_instance
        ? (await octopAgentsApi.privateInstance(expert.agent_id)).agent_id
        : expert.agent_id;
      if (request.current !== sequence) return;
      const next = await octopThreadsApi.continue(
        agentId,
        threadId,
        targetId,
        projectId,
      );
      await refresh({ silent: true });
      if (request.current === sequence)
        onContinued(next.agent_id, next.thread_id);
    } catch (cause) {
      if (request.current === sequence) {
        const reason = cause instanceof Error ? cause.message : "";
        const key = reason.includes("history_continuation_client_required")
          ? "updateClient"
          : reason.includes("history_continuation_server_required") ||
            reason.includes("404")
          ? "updateServer"
          : "failed";
        setError(t(`historyContinue.${key}`));
      }
    } finally {
      pending.current = false;
      if (request.current === sequence) setBusy(false);
    }
  };

  const action = (
    <Button
      type="link"
      size="small"
      className={styles.action}
      loading={busy}
      disabled={projects.busy}
      onClick={target ? () => void resume(target) : undefined}
    >
      {t("historyContinue.action")}
    </Button>
  );

  return (
    <div className={styles.notice}>
      {experts.length === 0 ? (
        <span>{t("historyContinue.noExpert")}</span>
      ) : target ? (
        action
      ) : (
        <Dropdown
          trigger={["click"]}
          disabled={busy || projects.busy}
          menu={{
            items: experts.map((expert) => ({
              key: expert.agent_id,
              label: expert.name,
              onClick: () => void resume(expert),
            })),
          }}
        >
          {action}
        </Dropdown>
      )}
      {(error || projects.error) && (
        <span role="alert">{error || projects.error}</span>
      )}
    </div>
  );
}
