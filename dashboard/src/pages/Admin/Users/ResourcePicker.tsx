import { useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Input,
  Space,
  Tag,
} from "antd";
import { GraduationCap, Puzzle, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  expertAssignmentsApi,
  type AssignedSkill,
  type ExpertAssignmentOverview,
} from "../../../api/modules/expertAssignments";
import { apiErrorMessage } from "../../../utils/apiError";
import ExpertDescription from "./ExpertDescription";
import styles from "./expertAssignments.module.less";

export function SkillChoices({
  skills,
  selected,
  setSelected,
  disabled = false,
  allowRemoveOnly = false,
  searchable = true,
  exclude = [],
}: {
  skills: AssignedSkill[];
  selected: string[];
  setSelected: (ids: string[]) => void;
  disabled?: boolean;
  allowRemoveOnly?: boolean;
  searchable?: boolean;
  exclude?: string[];
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("all");
  const available = skills.filter((s) => !exclude.includes(s.id));
  const categories = [...new Set(available.map((s) => s.category))];
  const activeCategory = categories.includes(category) ? category : "all";
  const categoryLabel = (value: string) =>
    value === "other"
      ? t("employeeResources.otherCategory")
      : value === "company-capabilities"
      ? t("employeeResources.companyCapabilities")
      : value;
  const candidates = available.filter(
    (s) =>
      (activeCategory === "all" || s.category === activeCategory) &&
      `${s.name} ${s.description} ${s.source_name}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <>
      {searchable && (
        <Input.Search
          aria-label={t("employeeResources.searchSkills")}
          placeholder={t("employeeResources.searchSkills")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          allowClear
        />
      )}
      <div className={styles.selectionHeading}>
        <span className={styles.hint}>
          {searchable
            ? t("employeeResources.skillSelected", { count: selected.length })
            : t("employeeResources.filterCategory")}
        </span>
        <Space>
          <Button
            type="link"
            size="small"
            disabled={disabled || allowRemoveOnly}
            onClick={() =>
              setSelected([
                ...new Set([...selected, ...candidates.map((x) => x.id)]),
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
            {t("employeeResources.clearExtras")}
          </Button>
        </Space>
      </div>
      {categories.length > 1 && (
        <Space wrap size={6} className={styles.categoryFilters}>
          <Button
            size="small"
            type={category === "all" ? "primary" : "default"}
            onClick={() => setCategory("all")}
          >
            {t("employeeResources.allCategories")}
          </Button>
          {categories.map((c) => (
            <Button
              key={c}
              size="small"
              type={category === c ? "primary" : "default"}
              onClick={() => setCategory(c)}
            >
              {categoryLabel(c)}
            </Button>
          ))}
        </Space>
      )}
      <div className={styles.candidates}>
        {candidates.map((skill) => (
          <Checkbox
            key={skill.id}
            className={`${styles.candidate} ${
              selected.includes(skill.id) ? styles.chosen : ""
            }`}
            checked={selected.includes(skill.id)}
            disabled={
              disabled || (allowRemoveOnly && !selected.includes(skill.id))
            }
            aria-label={skill.name}
            onChange={(e) =>
              setSelected(
                e.target.checked
                  ? [...selected, skill.id]
                  : selected.filter((x) => x !== skill.id),
              )
            }
          >
            <span className={styles.optionBody}>
              <Puzzle size={18} />
              <span>
                <strong>{skill.name}</strong>
                <span className={styles.hint}>{skill.description}</span>
                <span className={styles.hint}>
                  {categoryLabel(skill.category)} ·{" "}
                  {skill.source_name === "builtin"
                    ? t("employeeResources.builtin")
                    : skill.source_name === "company"
                    ? t("employeeResources.companyService")
                    : skill.source_name}
                </span>
              </span>
            </span>
          </Checkbox>
        ))}
      </div>
      {!candidates.length && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t("employeeResources.noSkills")}
        />
      )}
    </>
  );
}

export default function ResourcePicker({
  scopeId,
  scopeName,
  kind,
  data,
  close,
  onSaved,
}: {
  scopeId: string;
  scopeName: string;
  kind: "experts" | "skills" | "defaults";
  data: ExpertAssignmentOverview;
  close: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const pool = data.pools?.find((p) => p.scope_id === scopeId);
  const [selected, setSelected] = useState(
    kind === "experts"
      ? pool?.expert_ids ?? []
      : kind === "defaults"
      ? data.defaults ?? []
      : pool?.skill_ids ?? [],
  );
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const candidates = data.experts.filter((e) =>
    `${e.name} ${e.description ?? ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const allowed =
    kind === "experts"
      ? data.experts.map((x) => x.agent_id)
      : (data.skills ?? []).map((x) => x.id);
  const missing = selected.filter((id) => !allowed.includes(id));
  const people = data.employees.filter(
    (e) => (e.scope_id ?? "unassigned") === scopeId,
  );
  const affected =
    kind === "defaults"
      ? []
      : people.filter((e) =>
          (kind === "experts" ? e.expert_ids : e.skill_ids ?? []).some(
            (id) => !selected.includes(id),
          ),
        );
  const save = async () => {
    setSaving(true);
    setError("");
    try {
      if (kind === "defaults")
        await expertAssignmentsApi.defaults(selected, data.source.revision);
      else
        await expertAssignmentsApi.pool(scopeId, {
          expert_ids: kind === "experts" ? selected : pool?.expert_ids ?? [],
          skill_ids: kind === "skills" ? selected : pool?.skill_ids ?? [],
          expected_revision: pool?.revision ?? 0,
          expected_source_revision: data.source.revision,
        });
      setSaved(true);
      try {
        await onSaved();
        close();
      } catch {
        setError(t("expertAccess.savedRefreshFailed"));
      }
    } catch (e) {
      setError(apiErrorMessage(e, t("common.saveFailed"), t));
    } finally {
      setSaving(false);
    }
  };
  return (
    <Drawer
      open
      width={480}
      className={styles.resourceDrawer}
      title={
        <div>
          <small className={styles.hint}>
            {t("communication.companyStructure")} / {scopeName}
          </small>
          <div className={styles.drawerTitle}>
            {t(
              `employeeResources.${
                kind === "experts"
                  ? "departmentExperts"
                  : kind === "skills"
                  ? "departmentSkills"
                  : "systemDefaults"
              }`,
            )}
          </div>
        </div>
      }
      onClose={close}
      maskClosable={!saving}
      closable={!saving}
      footer={
        <div className={styles.drawerFooter}>
          <span className={styles.hint}>
            {t("employeeResources.selectedCount", { count: selected.length })}
          </span>
          <Button onClick={close} disabled={saving}>
            {t("communication.cancel")}
          </Button>
          <Button
            type="primary"
            onClick={() => void save()}
            loading={saving}
            disabled={saved || missing.length > 0}
          >
            {t("employeeResources.saveSettings")}
          </Button>
        </div>
      }
    >
      {error && (
        <Alert
          showIcon
          type={saved ? "warning" : "error"}
          message={error}
          style={{ marginBottom: 16 }}
        />
      )}
      <div className={styles.callout}>
        <ShieldCheck size={17} />
        <span>
          {t(
            kind === "defaults"
              ? "employeeResources.defaultHint"
              : "employeeResources.departmentHint",
          )}
        </span>
      </div>
      {!!affected.length && (
        <Alert
          showIcon
          type="warning"
          message={t("employeeResources.affectedEmployees", {
            count: affected.length,
            names: affected.map((e) => e.display_name || e.username).join("、"),
          })}
          style={{ marginBottom: 16 }}
        />
      )}
      {missing.map((id) => (
        <div className={styles.candidate} key={id}>
          <Tag>{t("expertAccess.unavailable")}</Tag>
          <Button
            danger
            size="small"
            onClick={() => setSelected(selected.filter((x) => x !== id))}
          >
            {t("expertAccess.removeUnavailable")}
          </Button>
        </div>
      ))}
      {kind !== "experts" ? (
        <SkillChoices
          skills={data.skills ?? []}
          selected={selected}
          setSelected={setSelected}
          disabled={saving || saved}
        />
      ) : (
        <>
          <Input.Search
            placeholder={t("expertAccess.search")}
            aria-label={t("expertAccess.search")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
          />
          <div className={styles.selectionHeading}>
            <span>
              {t("employeeResources.selectedCount", { count: selected.length })}
            </span>
            <Space>
              <Button
                type="link"
                size="small"
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
              <Button type="text" size="small" onClick={() => setSelected([])}>
                {t("expertAccess.clear")}
              </Button>
            </Space>
          </div>
          <div className={styles.candidates}>
            {candidates.map((e) => (
              <Checkbox
                key={e.agent_id}
                className={`${styles.candidate} ${
                  selected.includes(e.agent_id) ? styles.chosen : ""
                }`}
                checked={selected.includes(e.agent_id)}
                disabled={
                  saving ||
                  saved ||
                  (e.available === false && !selected.includes(e.agent_id))
                }
                aria-label={e.name}
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
                      {e.available === false
                        ? t("expertAccess.unavailable")
                        : t("employeeResources.providedCount", {
                            count: e.skill_ids?.length ?? 0,
                          })}
                    </span>
                  </span>
                </span>
              </Checkbox>
            ))}
          </div>
        </>
      )}
    </Drawer>
  );
}
