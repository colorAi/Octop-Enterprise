import { useEffect, useRef, useState } from "react";
import { Alert, Button, Modal, Select, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { useDesktopProjects } from "../context/DesktopProjectsContext";
import {
  notifyProjectsChanged,
  type ProjectImportResult,
} from "../api/desktopProjects";
import { getAuthToken } from "../api/request";
import { tokenSessionIdentity } from "../utils/authSession";

type Action = "export" | "import" | "sync" | "list" | "attach";

export default function ProjectMigrationSettings({
  online,
  canImport = online,
}: {
  online: boolean;
  canImport?: boolean;
}) {
  const { t } = useTranslation();
  const projects = useDesktopProjects();
  const [selected, setSelected] = useState<string[]>([]);
  const [accountProjects, setAccountProjects] = useState<
    { project_id: string; name: string }[] | null
  >(null);
  const [attachId, setAttachId] = useState("");
  const [busy, setBusy] = useState<Action | null>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const [pending, setPending] = useState(false);
  const sequence = useRef(0);
  useEffect(
    () => () => {
      sequence.current++;
    },
    [],
  );
  const bridge = window.__octopRuntime?.projects;
  const supported = Boolean(bridge?.export && bridge?.import);
  const options = Object.values(projects.state?.projects ?? {})
    .filter((p) => p.authorized && !p.hidden)
    .map((p) => ({ value: p.id, label: `${p.name} · ${p.path}` }));
  const chosen = selected.filter((id) => options.some((p) => p.value === id));
  const currentId = projects.state?.current_project_id;
  const syncIds =
    chosen.length > 0
      ? chosen
      : currentId && options.some((option) => option.value === currentId)
      ? [currentId]
      : [];

  const perform = async (action: Action) => {
    if (busy || !bridge || !(action === "import" ? canImport : online)) return;
    const ticket = ++sequence.current;
    const identity = tokenSessionIdentity(getAuthToken());
    const current = () =>
      sequence.current === ticket &&
      identity === tokenSessionIdentity(getAuthToken());
    setBusy(action);
    setError("");
    try {
      if (action === "list" && bridge.accountProjects) {
        const items = await bridge.accountProjects();
        if (current()) {
          setAttachId("");
          setAccountProjects(items);
        }
        return;
      }
      if (action === "sync" && bridge.sync) {
        const response = await bridge.sync({ project_ids: syncIds });
        if (current()) {
          notifyProjectsChanged();
          setPending(false);
          setResult(
            t("projectMigration.syncDone", { count: response.synced_count }),
          );
        }
        return;
      }
      if (action === "export" && bridge.export) {
        const response = await bridge.export({ project_ids: chosen });
        if (current() && !response.cancelled) {
          setPending(false);
          setResult(
            t("projectMigration.exportDone", { path: response.path ?? "" }),
          );
        }
        return;
      }

      let response: ProjectImportResult;
      if (action === "import" && bridge.import) {
        response = await bridge.import();
      } else if (action === "attach" && bridge.attach && attachId) {
        response = await bridge.attach({ project_id: attachId });
        if (current()) setAccountProjects(null);
      } else {
        return;
      }
      if (!current() || response.cancelled) return;
      if (response.project_ids?.length) setSelected(response.project_ids);
      notifyProjectsChanged();
      setPending(Boolean(response.sync_pending));
      setResult(
        t(
          action === "attach"
            ? response.sync_pending
              ? "projectMigration.attachPending"
              : "projectMigration.attachDone"
            : response.sync_pending
            ? "projectMigration.importPending"
            : "projectMigration.importDone",
          { path: response.path ?? "", count: response.synced_count ?? 0 },
        ),
      );
      if (response.sync_error) setError(response.sync_error);
    } catch (cause) {
      if (current())
        setError(
          cause instanceof Error ? cause.message : t("projectMigration.failed"),
        );
    } finally {
      if (current()) setBusy(null);
    }
  };
  const attachOptions = (accountProjects ?? [])
    .filter((p) => !projects.state?.projects[p.project_id])
    .map((p) => ({ value: p.project_id, label: p.name }));
  return (
    <section
      aria-label={t("projectMigration.title")}
      style={{ margin: "20px 0" }}
    >
      <Typography.Title level={5}>
        {t("projectMigration.title")}
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        {t("projectMigration.hint")}
      </Typography.Paragraph>
      {!supported && (
        <Alert type="info" message={t("projectMigration.updateClient")} />
      )}
      <Select
        mode="multiple"
        style={{ width: "100%", marginBottom: 12 }}
        aria-label={t("projectMigration.select")}
        placeholder={t("projectMigration.select")}
        value={chosen}
        options={options}
        onChange={setSelected}
        disabled={Boolean(busy) || !online || !supported}
      />
      <Space wrap>
        <Button
          onClick={() => void perform("export")}
          loading={busy === "export"}
          disabled={
            !online || !supported || Boolean(busy) || chosen.length === 0
          }
        >
          {t("projectMigration.export")}
        </Button>
        <Button
          onClick={() => void perform("import")}
          loading={busy === "import"}
          disabled={!canImport || !supported || Boolean(busy)}
        >
          {t("projectMigration.import")}
        </Button>
        {bridge?.sync && (
          <Button
            onClick={() => void perform("sync")}
            loading={busy === "sync"}
            disabled={!online || Boolean(busy) || syncIds.length === 0}
          >
            {t("projectMigration.sync")}
          </Button>
        )}
        {bridge?.accountProjects && bridge.attach && (
          <Button
            onClick={() => void perform("list")}
            loading={busy === "list"}
            disabled={!online || Boolean(busy)}
          >
            {t("projectMigration.attach")}
          </Button>
        )}
      </Space>
      {error && (
        <Alert style={{ marginTop: 12 }} type="error" message={error} />
      )}
      {result && (
        <Alert
          style={{ marginTop: 12 }}
          type={pending ? "warning" : "success"}
          message={result}
        />
      )}
      <Modal
        open={accountProjects !== null}
        title={t("projectMigration.attach")}
        okText={t("projectMigration.chooseFolder")}
        cancelText={t("common.cancel")}
        okButtonProps={{ disabled: !attachId }}
        confirmLoading={busy === "attach"}
        onCancel={() => {
          if (busy !== "attach") setAccountProjects(null);
        }}
        onOk={() => void perform("attach")}
      >
        <Select
          aria-label={t("projectMigration.selectAccountProject")}
          placeholder={t("projectMigration.selectAccountProject")}
          notFoundContent={t("projectMigration.noAccountProjects")}
          style={{ width: "100%" }}
          showSearch
          optionFilterProp="label"
          value={attachId || undefined}
          options={attachOptions}
          onChange={setAttachId}
        />
      </Modal>
    </section>
  );
}
