import { useState } from "react";
import { Alert, Button, Checkbox, Drawer, Select, Space } from "antd";
import { useTranslation } from "react-i18next";
import {
  expertAssignmentsApi,
  type ExpertAssignment,
  type ExpertAssignmentOverview,
} from "../../../api/modules/expertAssignments";
import type {
  CommunicationUser,
  Department,
} from "../../../api/types/communication";
import { apiErrorMessage } from "../../../utils/apiError";
import { SkillChoices } from "./ResourcePicker";
import styles from "./expertAssignments.module.less";
import ExpertDescription from "./ExpertDescription";

export default function BatchResourceDrawer({
  employees,
  people,
  departments,
  data,
  kind,
  close,
  onSaved,
}: {
  employees: ExpertAssignment[];
  people: CommunicationUser[];
  departments: Department[];
  data: ExpertAssignmentOverview;
  kind: "experts" | "skills" | "department";
  close: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<string[]>([]);
  const [target, setTarget] = useState<string>();
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const scope = employees[0]?.scope_id ?? "unassigned";
  const pool = data.pools?.find((p) => p.scope_id === scope);
  const name =
    departments.find((d) => d.department_id === scope)?.name ??
    t("communication.unassigned");
  const save = async () => {
    setSaving(true);
    setError("");
    let committed = false;
    try {
      if (kind === "department") {
        const failed: string[] = [];
        let succeeded = 0;
        for (const e of employees) {
          try {
            await expertAssignmentsApi.basic(e.id, {
              department_id: target === "unassigned" ? null : target ?? null,
              job_title_id:
                people.find((p) => p.user_id === e.communication_user_id)
                  ?.job_title_id ?? null,
              membership_revision: e.membership_revision ?? 0,
              expected_revision: e.revision,
              expected_source_revision: data.source.revision,
            });
            succeeded++;
          } catch {
            failed.push(e.display_name || e.username);
          }
        }
        setSaved(true);
        committed = true;
        await onSaved();
        if (failed.length) {
          setError(
            t("employeeResources.batchPartial", {
              count: succeeded,
              names: failed.join("、"),
            }),
          );
          return;
        }
      } else {
        await expertAssignmentsApi.employees(
          employees.map((e) => ({
            id: e.id,
            mode: "assigned_only",
            expert_ids: kind === "experts" ? selected : e.expert_ids,
            skill_ids: kind === "skills" ? selected : e.skill_ids ?? [],
            expected_revision: e.revision,
            scope_id: e.scope_id ?? scope,
            membership_revision: e.membership_revision ?? 0,
          })),
          data.source.revision,
        );
        setSaved(true);
        committed = true;
        await onSaved();
      }
      close();
    } catch (e) {
      setError(
        committed
          ? t("expertAccess.savedRefreshFailed")
          : apiErrorMessage(e, t("common.saveFailed"), t),
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <Drawer
      open
      width={480}
      className={styles.resourceDrawer}
      title={t(`employeeResources.batch.${kind}`)}
      onClose={close}
      maskClosable={!saving}
      footer={
        <div className={styles.drawerFooter}>
          <span className={styles.hint}>
            {t("employeeResources.batchCount", { count: employees.length })}
          </span>
          <Button onClick={close} disabled={saving}>
            {t("communication.cancel")}
          </Button>
          <Button
            type="primary"
            onClick={() => void save()}
            loading={saving}
            disabled={saved || (kind === "department" && !target)}
          >
            {t("employeeResources.saveSettings")}
          </Button>
        </div>
      }
    >
      <p>
        {name} · {employees.map((e) => e.display_name || e.username).join("、")}
      </p>
      <Alert
        type="warning"
        showIcon
        message={t(
          kind === "department"
            ? "employeeResources.batchMoveHint"
            : "employeeResources.batchReplaceHint",
        )}
        style={{ marginBottom: 20 }}
      />
      {error && (
        <Alert
          type={saved ? "warning" : "error"}
          showIcon
          message={error}
          style={{ marginBottom: 20 }}
        />
      )}
      {kind === "department" ? (
        <Select
          style={{ width: "100%" }}
          aria-label={t("communication.department")}
          value={target}
          onChange={setTarget}
          options={[
            { value: "unassigned", label: t("communication.unassigned") },
            ...departments
              .filter((d) => d.enabled)
              .map((d) => ({ value: d.department_id, label: d.name })),
          ]}
        />
      ) : kind === "skills" ? (
        <SkillChoices
          skills={(data.skills ?? []).filter(
            (s) => pool?.skill_ids.includes(s.id),
          )}
          selected={selected}
          setSelected={setSelected}
          disabled={saving || saved}
          exclude={data.defaults ?? []}
        />
      ) : (
        <>
          <div className={styles.selectionHeading}>
            <strong>
              {t("employeeResources.selectedCount", { count: selected.length })}
            </strong>
            <Space>
              <Button
                type="link"
                onClick={() =>
                  setSelected(
                    data.experts
                      .filter(
                        (e) =>
                          pool?.expert_ids.includes(e.agent_id) &&
                          e.available !== false,
                      )
                      .map((e) => e.agent_id),
                  )
                }
              >
                {t("employeeResources.selectAll")}
              </Button>
              <Button type="text" onClick={() => setSelected([])}>
                {t("expertAccess.clear")}
              </Button>
            </Space>
          </div>
          <div className={styles.candidates}>
            {data.experts
              .filter((e) => pool?.expert_ids.includes(e.agent_id))
              .map((e) => (
                <Checkbox
                  key={e.agent_id}
                  className={styles.candidate}
                  checked={selected.includes(e.agent_id)}
                  disabled={saving || saved || e.available === false}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? [...selected, e.agent_id]
                        : selected.filter((x) => x !== e.agent_id),
                    )
                  }
                >
                  <strong>{e.name}</strong>
                  <ExpertDescription description={e.description} />
                </Checkbox>
              ))}
          </div>
        </>
      )}
    </Drawer>
  );
}
