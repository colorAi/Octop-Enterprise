import { useEffect, useRef, useState } from "react";
import { message } from "@/utils/antdMessage";
import { requireDesktopRuntime } from "../api/desktopRuntime";
import { Alert, Button, Input, Space, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { useDesktopWorkspace } from "../context/DesktopWorkspaceContext";

export default function LocalWorkspaceSettings() {
  const { t } = useTranslation();
  const desktop = useDesktopWorkspace();
  const online = desktop.state?.state === "online";
  const [runtime, setRuntime] = useState<{
    tools?: Record<string, string>;
    active_tasks?: {
      task_id: string;
      thread_id: string;
      status?: string;
      recovery_state?: string;
      sync_state?: "pending" | "synced";
    }[];
    error?: string;
  } | null>(null);
  const [reconciling, setReconciling] = useState<string>();
  const [checking, setChecking] = useState(false);
  const [diagnosticRevision, setDiagnosticRevision] = useState(0);
  const manualToolCheck = useRef(false);
  useEffect(() => {
    setRuntime(null);
    setChecking(false);
    if (
      !desktop.available ||
      !online ||
      !desktop.state?.grant?.id ||
      desktop.error
    )
      return;
    let cancelled = false;
    const verifyTools = manualToolCheck.current;
    manualToolCheck.current = false;
    setChecking(true);
    void Promise.resolve()
      .then(() =>
        verifyTools
          ? requireDesktopRuntime().status({ verify_tools: true })
          : requireDesktopRuntime().status(),
      )
      .then((result) => {
        if (!cancelled)
          setRuntime({
            tools: result.tools,
            active_tasks: result.active_tasks,
          });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setRuntime({
            error:
              error instanceof Error
                ? error.message
                : t("devices.runtimeUnavailable"),
          });
      })
      .finally(() => {
        if (!cancelled) setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    desktop.available,
    online,
    desktop.state?.grant?.id,
    desktop.error,
    diagnosticRevision,
    t,
  ]);
  if (!desktop.available) return null;
  return (
    <section aria-label={t("devices.workspaceTitle")}>
      <Space style={{ marginBottom: 8 }}>
        <Typography.Text strong>{t("devices.workspaceTitle")}</Typography.Text>
        <Tag color={online ? "green" : "default"}>
          {t(`devices.workspaceState_${desktop.state?.state ?? "connecting"}`)}
        </Tag>
      </Space>
      <Typography.Paragraph type="secondary">
        {t("devices.workspaceHint")}
      </Typography.Paragraph>
      <Input
        readOnly
        aria-label={t("devices.workspaceTitle")}
        value={desktop.state?.path ?? ""}
        placeholder={t("devices.workspaceEmpty")}
      />
      <Space wrap style={{ margin: "12px 0" }}>
        <Button
          type="primary"
          disabled={!online || desktop.enabled === false}
          loading={desktop.busy}
          onClick={() => void desktop.choose()}
        >
          {t(
            desktop.state?.path
              ? "devices.workspaceChange"
              : "devices.workspaceChoose",
          )}
        </Button>
        {desktop.state?.path && (
          <Button
            disabled={!online || desktop.busy}
            onClick={() => void desktop.clear()}
          >
            {t("devices.workspaceClear")}
          </Button>
        )}
        {!online && (
          <Button disabled={desktop.busy} onClick={desktop.retry}>
            {t("common.retry")}
          </Button>
        )}
      </Space>
      <Typography.Paragraph type="secondary" style={{ fontSize: 12 }}>
        {t("devices.workspaceConsent")}
      </Typography.Paragraph>
      {online && desktop.state?.grant && (
        <div style={{ marginBottom: 12 }}>
          <Space wrap>
            <Typography.Text strong>
              {t("devices.runtimeTitle")}
            </Typography.Text>
            <Button
              size="small"
              loading={checking}
              disabled={checking}
              onClick={() => {
                manualToolCheck.current = true;
                setDiagnosticRevision((r) => r + 1);
              }}
            >
              {t("devices.runtimeCheck")}
            </Button>
            {runtime && !runtime.error && !checking && (
              <Tag color="green">{t("devices.runtimeReady")}</Tag>
            )}
          </Space>
          {runtime?.tools && (
            <details style={{ marginTop: 8 }}>
              <summary>{t("devices.runtimeDetails")}</summary>
              <Typography.Paragraph type="secondary">
                {Object.entries(runtime.tools)
                  .map(([name, version]) => `${name}: ${version}`)
                  .join(" · ")}
              </Typography.Paragraph>
            </details>
          )}
          {runtime?.active_tasks?.some(
            (task) => task.sync_state === "pending",
          ) && <Alert type="info" message={t("devices.runtimeSyncPending")} />}
          {runtime?.active_tasks
            ?.filter((task) => task.status === "unknown")
            .map((task) => (
              <Alert
                key={task.task_id}
                type="warning"
                message={t(
                  task.recovery_state === "missing"
                    ? "devices.runtimeTaskMissing"
                    : "devices.runtimeTaskUnknown",
                )}
                description={
                  <>
                    <Typography.Paragraph
                      type="secondary"
                      style={{ margin: "6px 0 10px", overflowWrap: "anywhere" }}
                    >
                      {task.thread_id}
                    </Typography.Paragraph>
                    <Button
                      size="small"
                      loading={reconciling === task.task_id}
                      disabled={!!reconciling && reconciling !== task.task_id}
                      onClick={() => {
                        const bridge = requireDesktopRuntime();
                        if (!bridge.reconcile) {
                          void message.error(
                            t("devices.runtimeReconcileUnsupported"),
                          );
                          return;
                        }
                        setReconciling(task.task_id);
                        void bridge
                          .reconcile({
                            thread_id: task.thread_id,
                            task_id: task.task_id,
                            resolution: "failed",
                          })
                          .then(() => setDiagnosticRevision((r) => r + 1))
                          .catch((err: unknown) =>
                            message.error(
                              err instanceof Error
                                ? err.message
                                : t("devices.runtimeReconcileFailed"),
                            ),
                          )
                          .finally(() => setReconciling(undefined));
                      }}
                    >
                      {t("devices.runtimeReconcileAction")}
                    </Button>
                  </>
                }
              />
            ))}
          {runtime?.error && (
            <Alert
              type="warning"
              showIcon
              message={runtime.error}
              description={t("devices.runtimeMessagingAvailable")}
            />
          )}
        </div>
      )}
      {desktop.enabled === false && (
        <Alert type="info" message={t("devices.disabled")} />
      )}
      {desktop.error && (
        <Alert
          type="warning"
          showIcon
          message={t(`devices.workspaceError_${desktop.error}`)}
        />
      )}
    </section>
  );
}
