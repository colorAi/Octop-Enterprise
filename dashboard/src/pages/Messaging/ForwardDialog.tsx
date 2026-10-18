import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Select, Space, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAgent } from "../../context/AgentContext";
import { isCompanyDesktop } from "../../api/desktopRuntime";
import {
  AUTH_TOKEN_CHANGED_EVENT,
  UNAUTHORIZED_EVENT,
} from "../../api/request";
import { useCommunication } from "./hooks/CommunicationContext";
import { authSessionIdentity } from "./hooks/communicationClient";
import { conversationCanSend } from "./hooks/messagingState";
import { communicationErrorText, conversationName } from "./communicationUi";
import {
  ForwardDelivery,
  type ForwardResult,
  type ForwardTarget,
} from "./forwardDelivery";
import {
  allForwardSelection,
  forwardSourceContent,
  hasForwardContent,
  selectForwardContent,
  type ForwardSource,
} from "./forwardContent";
import ForwardContentPicker from "./ForwardContentPicker";
import { CommunicationError } from "../../api/modules/communication";
import { apiErrorMessage } from "../../utils/apiError";

export default function ForwardDialog({
  source,
  onClose,
}: {
  source: ForwardSource;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { agents, setActiveAgent } = useAgent();
  const { state, client } = useCommunication();
  const [selected, setSelected] = useState<string[]>([]);
  const [targetsOpen, setTargetsOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState<ForwardResult[]>([]);
  const initialSource = useRef(source);
  const content = useMemo(
    () => forwardSourceContent(initialSource.current),
    [],
  );
  const [selection, setSelection] = useState(() =>
    allForwardSelection(content),
  );
  const [note, setNote] = useState("");
  const [started, setStarted] = useState(false);
  const hasSelection = hasForwardContent(
    selectForwardContent(content, selection),
  );
  const controller = useRef(new AbortController());
  const delivery = useRef<ForwardDelivery>();
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (controller.current.signal.aborted) {
      controller.current = new AbortController();
      delivery.current = undefined;
    }
    const identity = authSessionIdentity();
    const changed = () => {
      if (identity !== authSessionIdentity()) {
        controller.current.abort();
        close.current();
      }
    };
    const denied = () => {
      controller.current.abort();
      close.current();
    };
    window.addEventListener(AUTH_TOKEN_CHANGED_EVENT, changed);
    window.addEventListener(UNAUTHORIZED_EVENT, denied);
    return () => {
      controller.current.abort();
      window.removeEventListener(AUTH_TOKEN_CHANGED_EVENT, changed);
      window.removeEventListener(UNAUTHORIZED_EVENT, denied);
    };
  }, []);
  const targets = useMemo<ForwardTarget[]>(
    () => [
      ...Object.values(state.contacts)
        .filter(
          (person) =>
            person.user_id !== state.profile?.user_id &&
            person.can_message !== false &&
            (!person.status || person.status === "active"),
        )
        .map((person) => ({
          value: `contact:${person.user_id}`,
          id: person.user_id,
          kind: "contact" as const,
          label: [person.nickname, person.job_title]
            .filter(Boolean)
            .join(" · "),
        })),
      ...Object.values(state.conversations)
        .filter(
          (row) =>
            row.type === "group" &&
            conversationCanSend(row, state.profile, state.contacts),
        )
        .map((row) => ({
          value: `conversation:${row.conversation_id}`,
          id: row.conversation_id,
          kind: "conversation" as const,
          label: conversationName(row, state, t),
        })),
      ...agents
        .filter(
          (agent) =>
            agent.state === "running" &&
            agent.is_owner !== false &&
            !agent.bridge_disconnected &&
            (!isCompanyDesktop() || (!agent.bridge && agent.kind !== "team")),
        )
        .map((agent) => ({
          value: `agent:${agent.agent_id}`,
          id: agent.agent_id,
          kind: "agent" as const,
          label: agent.name,
          agent,
        })),
    ],
    [state, agents, t],
  );
  const forward = async () => {
    if (busy || !selected.length || !hasSelection || state.phase !== "ready")
      return;
    delivery.current ??= new ForwardDelivery(
      initialSource.current,
      controller.current.signal,
      { selection, note },
    );
    setStarted(true);
    setBusy(true);
    setError("");
    const failed: string[] = [];
    const errors: string[] = [];
    try {
      for (const value of selected) {
        if (controller.current.signal.aborted) return;
        const target = targets.find((target) => target.value === value);
        if (!target) {
          failed.push(value);
          errors.push(t("communication.forwardTargetChanged"));
          continue;
        }
        try {
          const result = await delivery.current!.send(target);
          if (controller.current.signal.aborted) return;
          setResults((previous) => [
            ...previous.filter((item) => item.target.value !== value),
            result,
          ]);
        } catch (error) {
          if (controller.current.signal.aborted) return;
          failed.push(value);
          errors.push(
            `${target.label}：${
              error instanceof CommunicationError
                ? communicationErrorText(error, t)
                : apiErrorMessage(
                    error,
                    t("communication.errors.unavailable"),
                    t,
                  )
            }`,
          );
        }
      }
      setSelected(failed);
      setError(errors.join("\n"));
      void client.synchronize().catch(() => undefined);
    } finally {
      if (!controller.current.signal.aborted) setBusy(false);
    }
  };
  return (
    <Modal
      open
      title={t("communication.forward")}
      onCancel={onClose}
      onOk={() => void forward()}
      confirmLoading={busy}
      closable={!busy}
      maskClosable={!busy}
      keyboard={!busy}
      cancelButtonProps={{ disabled: busy }}
      okText={t("communication.send")}
      cancelText={t(results.length ? "common.close" : "communication.cancel")}
      okButtonProps={{
        disabled: !selected.length || !hasSelection || state.phase !== "ready",
      }}
    >
      <Space direction="vertical" size="middle" style={{ width: "100%" }}>
        <ForwardContentPicker
          content={content}
          selection={selection}
          onChange={setSelection}
          disabled={busy || started}
        />
        {!hasSelection && (
          <Typography.Text type="warning">
            {t("communication.forwardSelectContent")}
          </Typography.Text>
        )}
        <Select
          mode="multiple"
          showSearch
          optionFilterProp="label"
          maxCount={50}
          aria-label={t("communication.forwardTargets")}
          placeholder={t("communication.forwardTargets")}
          style={{ width: "100%" }}
          value={selected}
          onChange={setSelected}
          open={targetsOpen}
          onOpenChange={setTargetsOpen}
          onSelect={() => setTargetsOpen(false)}
          disabled={busy}
          options={(["contact", "conversation", "agent"] as const).map(
            (kind) => ({
              label: t(`communication.forwardKinds.${kind}`),
              options: targets.filter(
                (target) =>
                  target.kind === kind &&
                  !results.some(
                    (result) => result.target.value === target.value,
                  ),
              ),
            }),
          )}
        />
        <div>
          <label htmlFor="forward-note">{t("communication.forwardNote")}</label>
          <Input.TextArea
            id="forward-note"
            aria-label={t("communication.forwardNote")}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            placeholder={t("communication.forwardNotePlaceholder")}
            autoSize={{ minRows: 2, maxRows: 5 }}
            maxLength={2000}
            showCount
            disabled={busy || started}
            style={{ marginTop: 6, marginBottom: 16 }}
          />
        </div>
        <Typography.Text type="secondary">
          {t(
            started
              ? "communication.forwardRetryHint"
              : "communication.forwardHint",
          )}
        </Typography.Text>
        {state.phase !== "ready" && (
          <Alert
            type="warning"
            message={t("communication.errors.unavailable")}
          />
        )}
        {error && (
          <Alert
            type="error"
            message={<span style={{ whiteSpace: "pre-wrap" }}>{error}</span>}
          />
        )}
        {results.map((result) => (
          <Alert
            key={result.target.value}
            type="success"
            message={t("communication.forwardSuccess", {
              name: result.target.label,
            })}
            action={
              result.path && (
                <Button
                  size="small"
                  onClick={() => {
                    if (result.target.kind === "agent")
                      setActiveAgent(result.target.id);
                    onClose();
                    navigate(result.path!);
                  }}
                >
                  {t("communication.forwardOpen")}
                </Button>
              )
            }
          />
        ))}
      </Space>
    </Modal>
  );
}
