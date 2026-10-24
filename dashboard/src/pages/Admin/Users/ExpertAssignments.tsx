import { useEffect, useState } from "react";
import { Alert, Button, Select, Space, Tag } from "antd";
import { UserRound } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  expertAssignmentsApi,
  type ExpertAssignment,
  type ExpertAssignmentOverview,
} from "../../../api/modules/expertAssignments";
import { apiErrorMessage } from "../../../utils/apiError";
import styles from "./expertAssignments.module.less";

export { useExpertAssignments } from "./useExpertAssignments";

export function ExpertAssignmentSource({
  data,
  onSaved,
  onInstalled,
  catalogReady = true,
}: {
  data: ExpertAssignmentOverview;
  onSaved: () => Promise<void>;
  onInstalled?: () => void;
  catalogReady?: boolean;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<number | undefined>(
    data.source.source_user_id ?? undefined,
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [savedRefreshFailed, setSavedRefreshFailed] = useState(false);
  useEffect(() => {
    setSelected(data.source.source_user_id ?? undefined);
    setSavedRefreshFailed(false);
    setError("");
  }, [data.source.source_user_id, data.source.revision]);
  const save = async () => {
    if (selected === undefined) return;
    setSaving(true);
    setError("");
    setSavedRefreshFailed(false);
    try {
      await expertAssignmentsApi.source(selected, data.source.revision);
      try {
        await onSaved();
      } catch {
        setSavedRefreshFailed(true);
        setError(t("expertAccess.savedRefreshFailed"));
      }
    } catch (error) {
      setError(apiErrorMessage(error, t("common.saveFailed"), t));
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className={styles.source}>
      <div className={styles.sourceHeading}>
        <UserRound size={16} />
        <strong>{t("expertAccess.source")}</strong>
      </div>
      <Space wrap>
        <Select
          aria-label={t("expertAccess.source")}
          size="small"
          style={{ minWidth: 150 }}
          placeholder={t("expertAccess.selectSource")}
          value={selected}
          disabled={saving || savedRefreshFailed}
          onChange={setSelected}
          options={data.administrators.map((u) => ({
            value: u.id,
            label: u.display_name || u.username,
          }))}
        />
        <Button
          size="small"
          onClick={() => void save()}
          loading={saving}
          disabled={
            selected === undefined ||
            selected === data.source.source_user_id ||
            savedRefreshFailed
          }
        >
          {t("communication.save")}
        </Button>
      </Space>
      <span className={styles.hint}>
        {catalogReady
          ? t("employeeResources.installedCount", {
              experts: data.experts.length,
              skills: data.skills?.length ?? 0,
            })
          : t("employeeResources.catalogPending")}
      </span>
      {onInstalled && (
        <Button
          type="link"
          size="small"
          className={styles.installedLink}
          onClick={onInstalled}
          disabled={!catalogReady}
        >
          {t("employeeResources.viewInstalled")}
        </Button>
      )}
      {error && (
        <Alert
          type={savedRefreshFailed ? "warning" : "error"}
          showIcon
          message={error}
        />
      )}
    </section>
  );
}

export function ExpertAssignmentTags({
  employee,
  data,
}: {
  employee?: ExpertAssignment;
  data?: ExpertAssignmentOverview;
}) {
  const { t } = useTranslation();
  if (!employee) return <span className={styles.hint}>—</span>;
  if (employee.mode === "legacy") return <Tag>{t("expertAccess.legacy")}</Tag>;
  if (!employee.expert_ids.length)
    return <Tag color="gold">{t("expertAccess.waiting")}</Tag>;
  return (
    <Space size={[0, 4]} wrap>
      {employee.expert_ids.map((id) => (
        <Tag
          key={id}
          color={
            data?.experts.some((e) => e.agent_id === id) ? "pink" : "default"
          }
        >
          {data?.experts.find((e) => e.agent_id === id)?.name ||
            t("expertAccess.unavailable")}
        </Tag>
      ))}
    </Space>
  );
}

export { default as ExpertAssignmentDrawer } from "./EmployeeResourceDrawer";
