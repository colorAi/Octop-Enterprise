import { useState } from "react";
import {
  Alert,
  App,
  Avatar,
  Button,
  Checkbox,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Tabs,
  Tag,
} from "antd";
import {
  Check,
  Eye,
  GraduationCap,
  LockKeyhole,
  Puzzle,
  ShieldCheck,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  expertAssignmentsApi,
  type ExpertAssignment,
  type ExpertAssignmentOverview,
} from "../../../api/modules/expertAssignments";
import { communicationApi } from "../../../api/modules/communication";
import {
  usersApi,
  type EmployeeModelSelection,
} from "../../../api/modules/users";
import EmployeeKnowledgePicker from "./EmployeeKnowledgePicker";
import EmployeeModelPicker from "./EmployeeModelPicker";
import type {
  CommunicationUser,
  Department,
  JobTitle,
} from "../../../api/types/communication";
import { apiErrorMessage, parseApiError } from "../../../utils/apiError";
import { SkillChoices } from "./ResourcePicker";
import ExpertDescription from "./ExpertDescription";
import styles from "./expertAssignments.module.less";

export type EmployeeTab =
  | "experts"
  | "skills"
  | "knowledge"
  | "models"
  | "basic";

export function effectiveSkills(
  data: ExpertAssignmentOverview,
  expertIds: string[],
  extras: string[],
) {
  const defaults = data.defaults ?? [];
  const provided = [
    ...new Set(
      data.experts
        .filter((e) => expertIds.includes(e.agent_id))
        .flatMap((e) => e.skill_ids ?? []),
    ),
  ].filter((id) => !defaults.includes(id));
  const extra = extras.filter(
    (id) => !defaults.includes(id) && !provided.includes(id),
  );
  return {
    defaults,
    provided,
    extra,
    total: new Set([...defaults, ...provided, ...extras]).size,
  };
}

export default function EmployeeResourceDrawer({
  employee,
  data,
  person,
  departments = [],
  jobTitles = [],
  initialTab = "experts",
  close,
  onSaved,
  onManagePool,
}: {
  employee: ExpertAssignment;
  data: ExpertAssignmentOverview;
  person?: CommunicationUser;
  departments?: Department[];
  jobTitles?: JobTitle[];
  initialTab?: EmployeeTab;
  close: () => void;
  onSaved: () => Promise<void>;
  onManagePool?: (scope: string, kind: "experts" | "skills") => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [tab, setTab] = useState<EmployeeTab>(initialTab);
  const [mode, setMode] = useState<ExpertAssignment["mode"]>(
    initialTab === "basic" ||
      initialTab === "knowledge" ||
      initialTab === "models"
      ? employee.mode
      : "assigned_only",
  );
  const [modeEdited, setModeEdited] = useState(false);
  const [selected, setSelected] = useState(employee.expert_ids);
  const [skillIds, setSkillIds] = useState(employee.skill_ids ?? []);
  const [knowledgeIds, setKnowledgeIds] = useState<string[] | null>(null);
  const [modelSelection, setModelSelection] =
    useState<EmployeeModelSelection | null>(null);
  const [departmentId, setDepartmentId] = useState(
    person?.department_id ??
      (employee.scope_id === "unassigned" ? null : employee.scope_id) ??
      null,
  );
  const [jobId, setJobId] = useState(person?.job_title_id ?? null);
  const [titles, setTitles] = useState(jobTitles);
  const [newTitle, setNewTitle] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [preview, setPreview] = useState(false);
  const scopeId = departmentId ?? "unassigned";
  const departmentName =
    departments.find((d) => d.department_id === departmentId)?.name ??
    t("communication.unassigned");
  const pool = data.pools?.find((p) => p.scope_id === scopeId);
  const expertIds = pool?.expert_ids ?? [];
  const skills = effectiveSkills(data, selected, skillIds);
  const candidates = data.experts.filter(
    (e) =>
      expertIds.includes(e.agent_id) &&
      `${e.name} ${e.description ?? ""}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const skillCandidates = (data.skills ?? []).filter((s) =>
    (pool?.skill_ids ?? []).includes(s.id),
  );
  const missing = selected.filter(
    (id) =>
      !expertIds.includes(id) ||
      !data.experts.some((e) => e.agent_id === id && e.available !== false),
  );
  const missingSkills = skillIds.filter(
    (id) =>
      !(pool?.skill_ids ?? []).includes(id) ||
      !(data.skills ?? []).some((s) => s.id === id),
  );
  const basicChanged =
    !!person &&
    (departmentId !== (person.department_id ?? null) ||
      jobId !== (person.job_title_id ?? null));
  const selectionChanged =
    JSON.stringify([...selected].sort()) !==
      JSON.stringify([...employee.expert_ids].sort()) ||
    JSON.stringify([...skillIds].sort()) !==
      JSON.stringify([...(employee.skill_ids ?? [])].sort());
  // Opening the expert tab proposes assigned-only mode for legacy accounts.
  // Merely visiting Knowledge must not commit that implicit expert-mode change.
  const accessOnlyTab = tab === "knowledge" || tab === "models";
  const effectiveMode =
    accessOnlyTab && !modeEdited && !selectionChanged ? employee.mode : mode;
  const resourceChanged = effectiveMode !== employee.mode || selectionChanged;
  const saveResourceSelection =
    tab === "experts" ||
    tab === "skills" ||
    resourceChanged ||
    (basicChanged && effectiveMode === "assigned_only");
  const disabled = saving || saved;
  const selectDepartment = (value: string | null) => {
    const target = data.pools?.find(
      (p) => p.scope_id === (value ?? "unassigned"),
    );
    setDepartmentId(value);
    setSelected(selected.filter((id) => target?.expert_ids.includes(id)));
    setSkillIds(skillIds.filter((id) => target?.skill_ids.includes(id)));
  };
  const save = async () => {
    setSaving(true);
    setError("");
    let basicSaved = false;
    let resourcesSaved = false;
    let knowledgeSaved = false;
    try {
      if (tab === "knowledge" && knowledgeIds === null) return;
      if (tab === "models" && modelSelection === null) return;
      let state = employee;
      if (basicChanged) {
        state = await expertAssignmentsApi.basic(employee.id, {
          department_id: departmentId,
          job_title_id: jobId,
          membership_revision: employee.membership_revision ?? 0,
          expected_revision: employee.revision,
          expected_source_revision: data.source.revision,
        });
        basicSaved = true;
      }
      if (saveResourceSelection) {
        await expertAssignmentsApi.employees(
          [
            {
              id: employee.id,
              mode: effectiveMode,
              expert_ids: effectiveMode === "assigned_only" ? selected : [],
              skill_ids: effectiveMode === "assigned_only" ? skillIds : [],
              expected_revision: state.revision,
              scope_id: state.scope_id ?? scopeId,
              membership_revision:
                state.membership_revision ?? employee.membership_revision ?? 0,
            },
          ],
          data.source.revision,
        );
        resourcesSaved = true;
      }
      if (knowledgeIds !== null) {
        await usersApi.setKnowledgeBases(employee.id, knowledgeIds);
        knowledgeSaved = true;
      }
      if (modelSelection !== null) {
        await usersApi.setModels(employee.id, modelSelection);
      }
      setSaved(true);
      try {
        await onSaved();
        void message.success(t("employeeResources.saved"));
        close();
      } catch {
        setError(t("expertAccess.savedRefreshFailed"));
      }
    } catch (e) {
      const partial = parseApiError(e)?.details?.refresh_required === true;
      const knowledgePartial = resourcesSaved && knowledgeIds !== null;
      const modelPartial =
        modelSelection !== null &&
        (basicSaved || resourcesSaved || knowledgeSaved);
      setError(
        modelPartial
          ? t("employeeModels.partialSaveFailed")
          : knowledgePartial
          ? t("employeeKnowledge.partialSaveFailed")
          : basicSaved || partial
          ? t("employeeResources.basicSavedResourceFailed")
          : apiErrorMessage(e, t("common.saveFailed"), t),
      );
      if (basicSaved || partial || knowledgePartial || modelPartial) {
        setSaved(true);
        try {
          await onSaved();
        } catch {
          /* Keep the partial-save notice and prevent stale resubmission. */
        }
      }
    } finally {
      setSaving(false);
    }
  };
  const skillName = (id: string) =>
    (data.skills ?? []).find((s) => s.id === id)?.name ??
    t("expertAccess.unavailable");
  const removedOnMove =
    departmentId !== (person?.department_id ?? null) &&
    employee.expert_ids.filter((id) => !selected.includes(id)).length +
      (employee.skill_ids ?? []).filter((id) => !skillIds.includes(id)).length;
  const expertBody = (
    <>
      <div className={styles.callout}>
        <ShieldCheck size={18} />
        <span>
          {t("employeeResources.employeeExpertHint", {
            department: departmentName,
            count: expertIds.length,
          })}
        </span>
      </div>
      <div className={styles.mode}>
        <span>{t("expertAccess.assignedOnly")}</span>
        <Switch
          checked={mode === "assigned_only"}
          disabled={disabled}
          onChange={(v) => {
            setModeEdited(true);
            setMode(v ? "assigned_only" : "legacy");
          }}
        />
      </div>
      {mode === "legacy" ? (
        <Alert type="info" message={t("expertAccess.legacyHint")} />
      ) : (
        <>
          <div className={styles.selectionHeading}>
            <span>
              {t("employeeResources.expertSelected", {
                count: selected.length,
                total: expertIds.length,
              })}
            </span>
            <Space>
              <Button
                type="link"
                size="small"
                disabled={disabled || employee.disabled}
                onClick={() =>
                  setSelected([
                    ...new Set([
                      ...selected,
                      ...candidates
                        .filter((e) => e.available !== false)
                        .map((e) => e.agent_id),
                    ]),
                  ])
                }
              >
                {t("employeeResources.selectAll")}
              </Button>
              <Button
                type="text"
                size="small"
                disabled={disabled}
                onClick={() => setSelected([])}
              >
                {t("expertAccess.clear")}
              </Button>
            </Space>
          </div>
          <Input.Search
            aria-label={t("expertAccess.search")}
            placeholder={t("expertAccess.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
          />
          {missing.map((id) => (
            <div className={styles.candidate} key={id}>
              <Tag>{t("expertAccess.unavailable")}</Tag>
              <span>
                {data.experts.find((e) => e.agent_id === id)?.name ?? id}
              </span>
              <Button
                danger
                size="small"
                disabled={disabled}
                onClick={() => setSelected(selected.filter((x) => x !== id))}
              >
                {t("expertAccess.removeUnavailable")}
              </Button>
            </div>
          ))}
          <div className={styles.candidates}>
            {candidates.map((e) => (
              <Checkbox
                key={e.agent_id}
                aria-label={e.name}
                className={`${styles.candidate} ${
                  selected.includes(e.agent_id) ? styles.chosen : ""
                }`}
                checked={selected.includes(e.agent_id)}
                disabled={
                  disabled ||
                  e.available === false ||
                  (employee.disabled && !selected.includes(e.agent_id))
                }
                onChange={(event) =>
                  setSelected(
                    event.target.checked
                      ? [...selected, e.agent_id]
                      : selected.filter((x) => x !== e.agent_id),
                  )
                }
              >
                <span className={styles.optionBody}>
                  <GraduationCap size={19} />
                  <span>
                    <strong>{e.name}</strong>
                    <ExpertDescription description={e.description} />
                    <span className={styles.hint}>
                      {t("employeeResources.providedCount", {
                        count: e.skill_ids?.length ?? 0,
                      })}
                    </span>
                  </span>
                </span>
              </Checkbox>
            ))}
          </div>
          {!expertIds.length && (
            <p className={styles.hint}>
              {t("employeeResources.configureDepartmentFirst")}
            </p>
          )}
          <p className={styles.hint}>
            {t("employeeResources.expertSkillsAutomatic")}
          </p>
          {onManagePool && (
            <Button
              type="link"
              style={{ padding: 0 }}
              onClick={() => onManagePool(scopeId, "experts")}
            >
              {t("employeeResources.manageDepartmentExperts")}
            </Button>
          )}
        </>
      )}
    </>
  );
  const skillsBody = (
    <>
      <div className={styles.callout}>
        <Puzzle size={18} />
        <span>{t("employeeResources.employeeSkillHint")}</span>
      </div>
      <section className={styles.skillSection}>
        <div className={styles.skillSectionHead}>
          <strong>
            <LockKeyhole size={14} />
            {t("employeeResources.systemDefaults")}
            <Tag>{skills.defaults.length}</Tag>
          </strong>
          <small>{t("employeeResources.defaultAutomatic")}</small>
        </div>
        <Space size={[4, 6]} wrap>
          {skills.defaults.map((id) => (
            <Tag key={id} icon={<LockKeyhole size={11} />}>
              {skillName(id)}
            </Tag>
          ))}
        </Space>
        {!skills.defaults.length && (
          <p className={styles.hint}>{t("employeeResources.noDefaults")}</p>
        )}
      </section>
      <section className={styles.skillSection}>
        <div className={styles.skillSectionHead}>
          <strong>
            <GraduationCap size={15} />
            {t("employeeResources.expertProvided")}
            <Tag>{skills.provided.length}</Tag>
          </strong>
          <small>{t("employeeResources.followExperts")}</small>
        </div>
        {skills.provided.map((id) => (
          <div className={styles.providedSkill} key={id}>
            <Check size={13} />
            <span>{skillName(id)}</span>
            <small>
              {data.experts
                .filter(
                  (e) =>
                    selected.includes(e.agent_id) && e.skill_ids?.includes(id),
                )
                .map((e) => e.name)
                .join("、")}
            </small>
          </div>
        ))}
        {!skills.provided.length && (
          <p className={styles.hint}>{t("employeeResources.noProvided")}</p>
        )}
      </section>
      <section className={styles.skillSection}>
        <div className={styles.skillSectionHead}>
          <strong>
            <Puzzle size={15} />
            {t("employeeResources.extraAssigned")}
          </strong>
          <small>
            {t("employeeResources.skillSelected", {
              count: skills.extra.length,
            })}
          </small>
        </div>
        <p className={styles.hint}>
          {t("employeeResources.extraHint", { department: departmentName })}
        </p>
        {missingSkills.map((id) => (
          <div key={id} className={styles.candidate}>
            <Tag>{t("expertAccess.unavailable")}</Tag>
            <span>{skillName(id)}</span>
            <Button
              danger
              size="small"
              disabled={disabled}
              onClick={() => setSkillIds(skillIds.filter((x) => x !== id))}
            >
              {t("expertAccess.removeUnavailable")}
            </Button>
          </div>
        ))}
        <SkillChoices
          skills={skillCandidates}
          selected={skillIds}
          setSelected={setSkillIds}
          disabled={disabled || mode === "legacy"}
          allowRemoveOnly={employee.disabled}
          searchable={false}
          exclude={[...skills.defaults, ...skills.provided]}
        />
        <p className={styles.hint}>{t("employeeResources.deduplicateHint")}</p>
        {onManagePool && (
          <Button
            type="link"
            style={{ padding: 0 }}
            onClick={() => onManagePool(scopeId, "skills")}
          >
            {t("employeeResources.manageDepartmentSkills")}
          </Button>
        )}
      </section>
    </>
  );
  const basicBody = (
    <Form layout="vertical">
      <Form.Item label={t("employeeResources.employeeName")}>
        <Input
          value={person?.nickname || employee.display_name || employee.username}
          readOnly
        />
        <span className={styles.hint}>
          {t("employeeResources.nameManagedElsewhere")}
        </span>
      </Form.Item>
      <Form.Item label={t("communication.department")}>
        <Select
          value={departmentId ?? "unassigned"}
          disabled={disabled}
          aria-label={t("communication.department")}
          onChange={(value) =>
            selectDepartment(value === "unassigned" ? null : value)
          }
          options={[
            { value: "unassigned", label: t("communication.unassigned") },
            ...departments
              .filter((d) => d.enabled || d.department_id === departmentId)
              .map((d) => ({ value: d.department_id, label: d.name })),
          ]}
        />
      </Form.Item>
      <Form.Item label={t("communication.jobTitle")}>
        <Select
          value={jobId ?? undefined}
          allowClear
          showSearch
          optionFilterProp="label"
          disabled={disabled}
          aria-label={t("communication.jobTitle")}
          placeholder={t("communication.noJobTitle")}
          onChange={(value) => setJobId(value ?? null)}
          options={titles.map((j) => ({
            value: j.job_title_id,
            label: j.name,
          }))}
          popupRender={(menu) => (
            <>
              {menu}
              <div className={styles.addPosition}>
                <Input
                  size="small"
                  maxLength={40}
                  value={newTitle}
                  onChange={(e) => setNewTitle(e.target.value)}
                  placeholder={t("communication.jobTitlePlaceholder")}
                />
                <Button
                  size="small"
                  disabled={!newTitle.trim()}
                  onClick={() => {
                    setSaving(true);
                    void communicationApi.organization
                      .createJobTitle(newTitle.trim())
                      .then((title) => {
                        setTitles([
                          ...titles.filter(
                            (x) => x.job_title_id !== title.job_title_id,
                          ),
                          title,
                        ]);
                        setJobId(title.job_title_id);
                        setNewTitle("");
                      })
                      .catch((e) =>
                        setError(apiErrorMessage(e, t("common.saveFailed"), t)),
                      )
                      .finally(() => setSaving(false));
                  }}
                >
                  {t("communication.addJobTitle")}
                </Button>
              </div>
            </>
          )}
        />
      </Form.Item>
      {!!removedOnMove && (
        <Alert
          showIcon
          type="warning"
          message={t("employeeResources.moveRemoves", { count: removedOnMove })}
        />
      )}
      <p className={styles.hint}>{t("employeeResources.moveHint")}</p>
    </Form>
  );
  return (
    <Drawer
      open
      width={480}
      className={styles.resourceDrawer}
      onClose={close}
      maskClosable={!saving}
      closable={!saving}
      title={
        <div>
          <small className={styles.hint}>
            {t("communication.companyStructure")} / {departmentName}
          </small>
          <div className={styles.drawerTitle}>
            {t(
              tab === "knowledge"
                ? "employeeKnowledge.title"
                : tab === "models"
                ? "employeeModels.title"
                : `employeeResources.employeeTitle.${tab}`,
            )}
          </div>
        </div>
      }
      footer={
        <div className={styles.drawerFooter}>
          {!accessOnlyTab && (
            <span className={styles.hint}>
              {t("employeeResources.skillTotal", { count: skills.total })}
              <small className={styles.hint}>
                {t("employeeResources.breakdown", {
                  defaults: skills.defaults.length,
                  provided: skills.provided.length,
                  extra: skills.extra.length,
                })}
              </small>
            </span>
          )}
          {!accessOnlyTab && (
            <Button
              type="text"
              size="small"
              aria-label={t("employeeResources.previewEmployee")}
              icon={<Eye size={17} />}
              disabled={mode === "legacy"}
              onClick={() => setPreview(true)}
            />
          )}
          <Button disabled={saving} onClick={close}>
            {t("communication.cancel")}
          </Button>
          <Button
            type="primary"
            loading={saving}
            disabled={
              saved ||
              (tab === "knowledge" && knowledgeIds === null) ||
              (tab === "models" && modelSelection === null) ||
              (saveResourceSelection &&
                ((effectiveMode === "assigned_only" &&
                  (missing.length > 0 || missingSkills.length > 0)) ||
                  (employee.disabled &&
                    (selected.length > 0 || skillIds.length > 0))))
            }
            onClick={() => void save()}
          >
            {t("employeeResources.saveSettings")}
          </Button>
        </div>
      }
    >
      <div className={styles.person}>
        <Avatar shape="square" size={42}>
          {(
            person?.nickname ||
            employee.display_name ||
            employee.username
          ).slice(0, 2)}
        </Avatar>
        <div>
          <strong>
            {person?.nickname || employee.display_name || employee.username}
          </strong>
          <div className={styles.hint}>
            {employee.username}
            {person?.job_title ? ` · ${person.job_title}` : ""}
          </div>
        </div>
        <Tag className={styles.personDepartment}>{departmentName}</Tag>
      </div>
      {error && (
        <Alert
          showIcon
          type={saved ? "warning" : "error"}
          message={error}
          style={{ marginBottom: 16 }}
        />
      )}
      {employee.disabled && (
        <Alert type="info" showIcon message={t("expertAccess.disabledHint")} />
      )}
      {!data.source.source_user_id && tab !== "basic" && !accessOnlyTab && (
        <Alert
          type="info"
          message={t("expertAccess.selectSourceFirst")}
          style={{ marginBottom: 16 }}
        />
      )}
      <Tabs
        activeKey={tab}
        onChange={(key) => setTab(key as EmployeeTab)}
        items={[
          {
            key: "experts",
            label: t("employeeResources.tabs.experts"),
            children: expertBody,
          },
          {
            key: "skills",
            label: t("employeeResources.tabs.skills"),
            children: skillsBody,
          },
          {
            key: "knowledge",
            label: t("employeeKnowledge.tab"),
            children: (
              <EmployeeKnowledgePicker
                userId={employee.id}
                disabled={disabled}
                onChange={setKnowledgeIds}
              />
            ),
          },
          {
            key: "models",
            label: t("employeeModels.tab"),
            children: (
              <EmployeeModelPicker
                userId={employee.id}
                disabled={disabled}
                onChange={setModelSelection}
              />
            ),
          },
          {
            key: "basic",
            label: t("employeeResources.tabs.basic"),
            children: basicBody,
          },
        ]}
      />
      <Modal
        open={preview}
        title={t("employeeResources.previewEmployee")}
        footer={
          <Button onClick={() => setPreview(false)}>{t("common.close")}</Button>
        }
        onCancel={() => setPreview(false)}
      >
        <p className={styles.hint}>{t("employeeResources.previewHint")}</p>
        <h4>{t("employeeResources.tabs.experts")}</h4>
        <Space size={[4, 6]} wrap>
          {selected.map((id) => (
            <Tag key={id}>
              {data.experts.find((e) => e.agent_id === id)?.name ??
                t("expertAccess.unavailable")}
            </Tag>
          ))}
        </Space>
        <h4 style={{ marginTop: 20 }}>{t("employeeResources.tabs.skills")}</h4>
        <Space size={[4, 6]} wrap>
          {[
            ...new Set([...skills.defaults, ...skills.provided, ...skillIds]),
          ].map((id) => (
            <Tag key={id}>{skillName(id)}</Tag>
          ))}
        </Space>
      </Modal>
    </Drawer>
  );
}
