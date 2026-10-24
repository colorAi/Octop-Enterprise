import { useEffect, useState, type ReactNode } from "react";
import { Button, Result, Select, Space } from "antd";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAgent } from "../context/AgentContext";
import { octopAgentsApi } from "../api/modules/octopAgents";
import { apiErrorMessage } from "../utils/apiError";
import PageLoading from "./PageLoading";
import { useConversationPageVisible } from "../hooks/useConversationPageVisible";

export default function PrivateExpertBoundary({
  children,
}: {
  children: ReactNode;
}) {
  const pageVisible = useConversationPageVisible();
  const { agentId } = useParams();
  const { agents, activeAgentId, loading, refresh, setActiveAgent } =
    useAgent();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const selected = agents.find(
    (agent) => agent.agent_id === (agentId || activeAgentId),
  );
  const source = selected?.requires_private_instance
    ? selected.agent_id
    : undefined;
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!source || !pageVisible) return;
    const controller = new AbortController();
    setError("");
    void octopAgentsApi
      .privateInstance(source, controller.signal)
      .then(async (result) => {
        if (controller.signal.aborted) return;
        setActiveAgent(result.agent_id);
        navigate(`/chat/${encodeURIComponent(result.agent_id)}`, {
          replace: true,
        });
        await refresh({ silent: true, force: true });
      })
      .catch((err) => {
        if (!controller.signal.aborted)
          setError(apiErrorMessage(err, t("common.loadFailed"), t));
      });
    return () => controller.abort();
  }, [source, attempt, refresh, setActiveAgent, navigate, t, pageVisible]);
  if (source && error)
    return (
      <Result
        status="warning"
        title={selected?.name}
        subTitle={error}
        extra={
          <Space direction="vertical">
            <Button
              type="primary"
              onClick={() => setAttempt((value) => value + 1)}
            >
              {t("communication.retry")}
            </Button>
            <Select
              aria-label={t("chat.pickAgent")}
              placeholder={t("chat.pickAgent")}
              style={{ minWidth: 240 }}
              options={agents
                .filter((agent) => agent.agent_id !== source)
                .map((agent) => ({ value: agent.agent_id, label: agent.name }))}
              onChange={(id: string) => {
                setActiveAgent(id);
                navigate(`/chat/${encodeURIComponent(id)}`);
              }}
            />
          </Space>
        }
      />
    );
  if (source || loading) return <PageLoading />;
  return children;
}
