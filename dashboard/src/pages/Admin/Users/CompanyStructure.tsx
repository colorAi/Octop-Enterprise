import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Alert,
  App,
  Avatar,
  Button,
  Collapse,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
} from "antd";
import {
  ArrowUpRight,
  Building2,
  ChevronDown,
  ChevronsUpDown,
  FolderOpen,
  GraduationCap,
  Inbox,
  LockKeyhole,
  Plus,
  Puzzle,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { communicationApi } from "../../../api/modules/communication";
import type {
  CommunicationUser,
  Department,
  JobTitle,
} from "../../../api/types/communication";
import {
  expertAssignmentsApi,
  type ExpertAssignment,
} from "../../../api/modules/expertAssignments";
import { useCommunication } from "../../Messaging/hooks/CommunicationContext";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { communicationError } from "../../../api/modules/communication";
import { communicationErrorText } from "../../Messaging/communicationUi";
import {
  ExpertAssignmentSource,
  useExpertAssignments,
} from "./ExpertAssignments";
import EmployeeResourceDrawer, {
  effectiveSkills,
  type EmployeeTab,
} from "./EmployeeResourceDrawer";
import ResourcePicker from "./ResourcePicker";
import BatchResourceDrawer from "./BatchResourceDrawer";
import styles from "./expertAssignments.module.less";

const org = communicationApi.organization;
type DepartmentForm = Pick<
  Department,
  "name" | "parent_id" | "sort_order" | "enabled"
>;

export default function CompanyStructure({
  renderGroup,
}: {
  renderGroup: (id: string, close: () => void) => ReactNode;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const user = useCurrentUser();
  const { client } = useCommunication();
  const isAdmin = user?.role === "admin";
  const assignments = useExpertAssignments(isAdmin, user?.id);
  const data = assignments.data;
  const [departments, setDepartments] = useState<Department[]>([]);
  const [people, setPeople] = useState<CommunicationUser[]>([]);
  const [titles, setTitles] = useState<JobTitle[]>([]);
  const [name, setName] = useState("");
  const [companySettings, setCompanySettings] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [expanded, setExpanded] = useState<string[]>();
  const [activeScope, setActiveScope] = useState<string>();
  const [selected, setSelected] = useState<string[]>([]);
  const [batch, setBatch] = useState<"experts" | "skills" | "department">();
  const [editor, setEditor] = useState<{
    person: CommunicationUser;
    employee: ExpertAssignment;
    tab: EmployeeTab;
  }>();
  const [picker, setPicker] = useState<{
    scope: string;
    kind: "experts" | "skills" | "defaults";
  }>();
  const [editing, setEditing] = useState<Department | null | undefined>();
  const [group, setGroup] = useState("");
  const [openingGroup, setOpeningGroup] = useState("");
  const [installed, setInstalled] = useState(false);
  const [form] = Form.useForm<DepartmentForm>();
  const controller = useRef(new AbortController());
  const detailRequest = useRef(0);
  const load = useCallback(async () => {
    controller.current.abort();
    controller.current = new AbortController();
    const signal = controller.current.signal;
    setLoading(true);
    setError("");
    const accept = <T,>(promise: Promise<T>, update: (value: T) => void) =>
      promise
        .then((value) => {
          if (!signal.aborted) update(value);
        })
        .catch((e) => {
          if (!signal.aborted) setError(communicationErrorText(e, t));
          throw e;
        });
    const results = await Promise.allSettled([
      accept(org.departments(signal), (value) => setDepartments(value.items)),
      accept(org.users(signal), (value) => {
        setPeople(value.items);
        setSelected([]);
      }).finally(() => {
        if (!signal.aborted) setLoading(false);
      }),
      accept(org.directorySettings(signal), (value) => setName(value.name)),
      accept(org.jobTitles(signal), (value) => setTitles(value.items)),
    ]);
    if (!signal.aborted)
      for (const result of results)
        if (result.status === "rejected") throw result.reason;
  }, [t]);
  useEffect(() => {
    setDepartments([]);
    setPeople([]);
    setTitles([]);
    setName("");
    setEditor(undefined);
    setPicker(undefined);
    setBatch(undefined);
    setInstalled(false);
    setPreparing(false);
    detailRequest.current++;
    setSelected([]);
    setGroup("");
    setExpanded(undefined);
    void load().catch(() => {});
    return () => controller.current.abort();
  }, [load, t, user?.id]);
  const refresh = async () => {
    const directory = load();
    const signal = controller.current.signal;
    const results = await Promise.allSettled([
      directory,
      isAdmin ? assignments.reload(signal) : Promise.resolve(),
    ]);
    if (!signal.aborted)
      for (const result of results)
        if (result.status === "rejected") throw result.reason;
  };
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
      await client.synchronize({ throwOnError: true });
      if (isAdmin)
        void assignments
          .reload(controller.current.signal)
          .catch((e) => setError(communicationErrorText(e, t)));
    } catch (e) {
      setError(communicationErrorText(e, t));
      throw e;
    } finally {
      if (!controller.current.signal.aborted) setBusy(false);
    }
  };
  const edit = (department: Department | null) => {
    form.setFieldsValue(
      department ?? { name: "", parent_id: null, sort_order: 0, enabled: true },
    );
    setEditing(department);
  };
  const openGroup = async (dept: Department) => {
    setOpeningGroup(dept.department_id);
    let opened = false;
    try {
      await run(async () => {
        const result = dept.conversation_id
          ? { conversation_id: dept.conversation_id }
          : await org.createGroup(
              dept.department_id,
              controller.current.signal,
            );
        setGroup(result.conversation_id);
        opened = true;
        if (!dept.conversation_id)
          void message.success(t("communication.departmentGroupCreated"));
      });
    } catch (e) {
      const detail = opened
        ? t("communication.groupRefreshFailed")
        : communicationError(e).code === "invalid_state"
        ? t("communication.departmentGroupNeedsMembers")
        : communicationErrorText(e, t);
      setError(detail);
      if (opened) void message.warning(detail);
      else void message.error(detail);
    } finally {
      setOpeningGroup("");
    }
  };
  const users = people.filter((p) => !p.is_admin);
  const employeeFor = (p: CommunicationUser) =>
    data?.employees.find((e) => e.communication_user_id === p.user_id);
  const scopeFor = (p: CommunicationUser | undefined) =>
    p?.department_id ?? "unassigned";
  const scopes = [
    ...departments.map((d) => ({
      id: d.department_id,
      name: d.name,
      department: d,
    })),
    ...[
      ...new Set(
        users.map((p) => p.department_id).filter((id): id is string => !!id),
      ),
    ]
      .filter((id) => !departments.some((d) => d.department_id === id))
      .map((id) => ({
        id,
        name: t("employeeResources.loadingDepartment"),
        department: undefined,
      })),
    {
      id: "unassigned",
      name: t("communication.unassigned"),
      department: undefined,
    },
  ];
  const scopeName = (id: string) =>
    scopes.find((s) => s.id === id)?.name ?? t("communication.unassigned");
  const poolFor = (id: string) => data?.pools?.find((p) => p.scope_id === id);
  const matches = (p: CommunicationUser) => {
    const e = employeeFor(p);
    const resources = e
      ? [
          ...e.expert_ids.map(
            (id) => data?.experts.find((x) => x.agent_id === id)?.name ?? "",
          ),
          ...(e.skill_ids ?? []).map(
            (id) => data?.skills?.find((x) => x.id === id)?.name ?? "",
          ),
        ].join(" ")
      : "";
    return (
      (status === "all" ||
        (status === "active"
          ? p.status === "active"
          : p.status !== "active")) &&
      `${p.nickname} ${p.job_title ?? ""} ${resources}`
        .toLowerCase()
        .includes(search.toLowerCase())
    );
  };
  const filtered = users.filter(matches);
  const visible = scopes.filter(
    (s) => !search || filtered.some((p) => scopeFor(p) === s.id),
  );
  const keys = search
    ? visible.map((s) => s.id)
    : expanded ?? [
        scopes.find((s) => users.some((p) => scopeFor(p) === s.id))?.id ??
          "unassigned",
      ];
  const selectedEmployees = selected
    .map((id) => users.find((p) => p.user_id === id))
    .filter((p): p is CommunicationUser => !!p)
    .map(employeeFor)
    .filter((e): e is ExpertAssignment => !!e);
  const openEmployee = async (p: CommunicationUser, tab: EmployeeTab) => {
    const e = employeeFor(p);
    if (!e || !assignments.catalogReady) return;
    const signal = controller.current.signal;
    const request = ++detailRequest.current;
    setPreparing(true);
    try {
      const employee = await expertAssignmentsApi.employee(e.id, signal);
      if (signal.aborted || request !== detailRequest.current) return;
      assignments.updateEmployees([employee]);
      setEditor({
        person: {
          ...p,
          department_id:
            employee.scope_id === "unassigned"
              ? null
              : employee.scope_id ?? null,
        },
        employee,
        tab,
      });
    } catch (e) {
      if (!signal.aborted && request === detailRequest.current)
        setError(communicationErrorText(e, t));
    } finally {
      if (request === detailRequest.current) setPreparing(false);
    }
  };
  const openBatch = async (kind: "experts" | "skills" | "department") => {
    const signal = controller.current.signal;
    const request = ++detailRequest.current;
    setPreparing(true);
    try {
      const employees: ExpertAssignment[] = [];
      for (const employee of selectedEmployees) {
        if (signal.aborted || request !== detailRequest.current) return;
        employees.push(
          await expertAssignmentsApi.employee(employee.id, signal),
        );
      }
      if (signal.aborted || request !== detailRequest.current) return;
      assignments.updateEmployees(employees);
      if (new Set(employees.map((e) => e.scope_id)).size !== 1) {
        setError(t("expertAccess.errors.batch_same_department_required"));
        return;
      }
      setBatch(kind);
    } catch (e) {
      if (!signal.aborted && request === detailRequest.current)
        setError(communicationErrorText(e, t));
    } finally {
      if (request === detailRequest.current) setPreparing(false);
    }
  };
  const descendants = new Set(editing ? [editing.department_id] : []);
  let changed = true;
  while (changed) {
    changed = false;
    for (const d of departments)
      if (
        d.parent_id &&
        descendants.has(d.parent_id) &&
        !descendants.has(d.department_id)
      ) {
        descendants.add(d.department_id);
        changed = true;
      }
  }
  const saveDepartment = async () => {
    const values = await form.validateFields();
    await run(() =>
      editing
        ? org.update(
            editing.department_id,
            { ...values, parent_id: values.parent_id ?? null },
            controller.current.signal,
          )
        : org.create(
            { ...values, parent_id: values.parent_id ?? null },
            controller.current.signal,
          ),
    );
    setEditing(undefined);
  };
  return (
    <div className={styles.structure}>
      <div className={styles.sectionHeading}>
        <div>
          <h2>{t("communication.companyStructure")}</h2>
          <p>{t("employeeResources.structureDescription")}</p>
        </div>
        <Space>
          <Tooltip title={t("employeeResources.companySettings")}>
            <Button
              type="text"
              icon={<Settings2 size={16} />}
              onClick={() => setCompanySettings(true)}
            />
          </Tooltip>
          <Button
            type="primary"
            icon={<Plus size={15} />}
            onClick={() => edit(null)}
          >
            {t("communication.createDepartment")}
          </Button>
        </Space>
      </div>
      {(error || assignments.error) && (
        <Alert
          type="error"
          showIcon
          message={error || assignments.error}
          action={
            <Button size="small" onClick={() => void refresh().catch(() => {})}>
              {t("common.retry")}
            </Button>
          }
          style={{ marginBottom: 16 }}
        />
      )}
      {isAdmin && data && (
        <ExpertAssignmentSource
          data={data}
          onSaved={assignments.reload}
          onInstalled={() => setInstalled(true)}
          catalogReady={assignments.catalogReady}
        />
      )}
      <div className={styles.departmentTiles}>
        {scopes.map((scope, index) => {
          const pool = poolFor(scope.id);
          return (
            <button
              className={`${styles.departmentTile} ${
                (activeScope ?? keys[0]) === scope.id ? styles.activeTile : ""
              }`}
              key={scope.id}
              onClick={() => {
                setActiveScope(scope.id);
                setExpanded([scope.id]);
                requestAnimationFrame(
                  () =>
                    document
                      .getElementById(`assignment-scope-${scope.id}`)
                      ?.scrollIntoView({ behavior: "smooth", block: "start" }),
                );
              }}
            >
              <div className={styles.tileHeading}>
                <span className={styles.tileIcon}>
                  {scope.id === "unassigned" ? (
                    <Inbox size={18} />
                  ) : index === 1 ? (
                    <Building2 size={18} />
                  ) : (
                    <FolderOpen size={18} />
                  )}
                </span>
                <strong>{scope.name}</strong>
                <ArrowUpRight size={14} />
              </div>
              <div className={styles.tileStats}>
                <b>{users.filter((p) => scopeFor(p) === scope.id).length}</b>{" "}
                {t("communication.people")}
                <span>·</span>
                {pool?.expert_ids.length ?? 0}{" "}
                {t("employeeResources.expertUnit")}
                <span>·</span>
                {pool?.skill_ids.length ?? 0} {t("employeeResources.skillUnit")}
              </div>
            </button>
          );
        })}
      </div>
      <div className={styles.peopleToolbar}>
        <h3>
          {t("communication.assignPeople")} <Tag>{users.length}</Tag>
        </h3>
        <Space wrap>
          <Input
            prefix={<Search size={14} />}
            aria-label={t("employeeResources.searchPeople")}
            placeholder={t("employeeResources.searchPeople")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
            style={{ width: 240 }}
          />
          <Select
            aria-label={t("communication.status")}
            value={status}
            onChange={setStatus}
            options={[
              { value: "all", label: t("employeeResources.allStatuses") },
              { value: "active", label: t("communication.active") },
              { value: "disabled", label: t("communication.disabled") },
            ]}
          />
          <Button
            type="text"
            icon={<ChevronsUpDown size={15} />}
            onClick={() =>
              setExpanded(
                keys.length === scopes.length ? [] : scopes.map((s) => s.id),
              )
            }
          >
            {t(
              keys.length === scopes.length
                ? "employeeResources.collapseAll"
                : "employeeResources.expandAll",
            )}
          </Button>
          <Tooltip title={t("communication.refresh")}>
            <Button
              type="text"
              aria-label={t("communication.refresh")}
              icon={<RefreshCw size={14} />}
              loading={loading}
              onClick={() =>
                void refresh().catch((e) =>
                  setError(communicationErrorText(e, t)),
                )
              }
            />
          </Tooltip>
        </Space>
      </div>
      {!!selected.length && (
        <div className={styles.batchBar}>
          <span>
            {t("employeeResources.batchCount", { count: selected.length })}
          </span>
          <Button
            size="small"
            onClick={() => void openBatch("experts")}
            disabled={
              preparing ||
              !assignments.catalogReady ||
              selectedEmployees.length !== selected.length
            }
          >
            {t("employeeResources.batch.experts")}
          </Button>
          <Button
            size="small"
            onClick={() => void openBatch("skills")}
            disabled={
              preparing ||
              !assignments.catalogReady ||
              selectedEmployees.length !== selected.length
            }
          >
            {t("employeeResources.batch.skills")}
          </Button>
          <Button
            size="small"
            onClick={() => void openBatch("department")}
            disabled={
              preparing ||
              !assignments.catalogReady ||
              selectedEmployees.length !== selected.length
            }
          >
            {t("employeeResources.batch.department")}
          </Button>
          <Button size="small" type="text" onClick={() => setSelected([])}>
            {t("expertAccess.clear")}
          </Button>
        </div>
      )}
      <Collapse
        className={styles.peopleGroups}
        activeKey={keys}
        onChange={(values) =>
          setExpanded(Array.isArray(values) ? values : [values])
        }
        items={visible.map((scope) => {
          const pool = poolFor(scope.id);
          const members = filtered.filter((p) => scopeFor(p) === scope.id);
          return {
            key: scope.id,
            label: (
              <Space
                id={`assignment-scope-${scope.id}`}
                className={styles.groupLabel}
              >
                <FolderOpen size={17} />
                <strong>{scope.name}</strong>
                <Tag>
                  {members.length} {t("communication.people")}
                </Tag>
                {scope.id === "unassigned" && (
                  <span className={styles.hint}>
                    {t("employeeResources.unassignedHint")}
                  </span>
                )}
              </Space>
            ),
            extra: (
              <Space size="middle" className={styles.groupActions}>
                <span>
                  <GraduationCap size={13} />
                  {pool?.expert_ids.length ?? 0}{" "}
                  {t("employeeResources.expertUnit")}
                </span>
                <span>
                  <Puzzle size={13} />
                  {pool?.skill_ids.length ?? 0}{" "}
                  {t("employeeResources.skillUnit")}
                </span>
                {scope.department && (
                  <Button
                    type="text"
                    size="small"
                    icon={<SlidersHorizontal size={14} />}
                    onClick={(e) => {
                      e.stopPropagation();
                      edit(scope.department!);
                    }}
                  >
                    {t("employeeResources.departmentSettings")}
                  </Button>
                )}
                {scope.department && (
                  <Button
                    type="text"
                    size="small"
                    disabled={busy || !scope.department.enabled}
                    loading={openingGroup === scope.id}
                    onClick={(event) => {
                      event.stopPropagation();
                      void openGroup(scope.department!);
                    }}
                  >
                    {t(
                      scope.department.conversation_id
                        ? "communication.manageGroup"
                        : "communication.createGroup",
                    )}
                  </Button>
                )}
              </Space>
            ),
            children: (
              <>
                {isAdmin && data && (
                  <div className={styles.departmentPool}>
                    {(["experts", "skills"] as const).map((kind) => {
                      const ids =
                        kind === "experts"
                          ? pool?.expert_ids ?? []
                          : pool?.skill_ids ?? [];
                      return (
                        <div key={kind} className={styles.poolRow}>
                          <div className={styles.poolHeading}>
                            <span>
                              {kind === "experts" ? (
                                <ShieldCheck size={14} />
                              ) : (
                                <Puzzle size={14} />
                              )}{" "}
                              {t(
                                kind === "experts"
                                  ? "employeeResources.departmentExperts"
                                  : "employeeResources.departmentSkills",
                              )}
                              <Tag>{ids.length}</Tag>
                            </span>
                            <Button
                              type="link"
                              size="small"
                              icon={<Plus size={14} />}
                              disabled={!assignments.catalogReady}
                              onClick={() =>
                                setPicker({ scope: scope.id, kind })
                              }
                            >
                              {t(
                                kind === "experts"
                                  ? "employeeResources.manageExperts"
                                  : "employeeResources.manageSkills",
                              )}
                            </Button>
                          </div>
                          <div className={styles.poolContent}>
                            <Space size={[4, 6]} wrap>
                              {ids.map((id) => (
                                <Tag
                                  className={
                                    kind === "experts"
                                      ? styles.expertChip
                                      : styles.skillChip
                                  }
                                  key={id}
                                >
                                  {kind === "experts"
                                    ? data.experts.find(
                                        (e) => e.agent_id === id,
                                      )?.name ?? t("expertAccess.unavailable")
                                    : data.skills?.find((s) => s.id === id)
                                        ?.name ?? t("expertAccess.unavailable")}
                                </Tag>
                              ))}
                              {!ids.length && (
                                <span className={styles.hint}>
                                  {t("employeeResources.noDepartmentResources")}
                                </span>
                              )}
                            </Space>
                            <span className={styles.poolNote}>
                              {t(
                                kind === "experts"
                                  ? "employeeResources.poolExpertNote"
                                  : "employeeResources.poolSkillNote",
                              )}
                            </span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
                <Table<CommunicationUser>
                  className={styles.employeeTable}
                  rowKey="user_id"
                  size="middle"
                  loading={loading}
                  dataSource={members}
                  scroll={{ x: isAdmin ? 960 : 620 }}
                  pagination={{ pageSize: 20, hideOnSinglePage: true }}
                  rowSelection={
                    isAdmin
                      ? {
                          selectedRowKeys: selected,
                          onChange: (values) => setSelected(values.map(String)),
                          getCheckboxProps: (person) => ({
                            disabled:
                              preparing ||
                              !assignments.catalogReady ||
                              !employeeFor(person) ||
                              (selected.length > 0 &&
                                !selected.includes(person.user_id) &&
                                scopeFor(
                                  users.find((p) => p.user_id === selected[0]),
                                ) !== scope.id),
                          }),
                        }
                      : undefined
                  }
                  columns={[
                    {
                      title: t("employeeResources.employee"),
                      width: 178,
                      render: (_, p) => (
                        <div className={styles.employeeIdentity}>
                          <Avatar shape="square" size={32}>
                            {p.nickname.slice(0, 2)}
                          </Avatar>
                          <div>
                            <strong>{p.nickname}</strong>
                            <small>
                              {employeeFor(p)?.username ??
                                p.user_id.split(":").at(-1)}
                            </small>
                          </div>
                        </div>
                      ),
                    },
                    {
                      title: t("communication.status"),
                      width: 85,
                      render: (_, p) => (
                        <span className={styles.statusLabel}>
                          <i
                            className={
                              p.status === "active"
                                ? styles.activeDot
                                : styles.inactiveDot
                            }
                          />
                          {t(
                            p.status === "active"
                              ? "communication.active"
                              : "communication.disabled",
                          )}
                        </span>
                      ),
                    },
                    {
                      title: t("communication.jobTitle"),
                      width: 135,
                      render: (_, p) =>
                        isAdmin ? (
                          <Button
                            type="text"
                            size="small"
                            className={styles.positionButton}
                            disabled={
                              preparing ||
                              !assignments.catalogReady ||
                              !employeeFor(p)
                            }
                            onClick={() => openEmployee(p, "basic")}
                          >
                            {p.job_title || t("communication.noJobTitle")}
                            <ChevronDown size={12} />
                          </Button>
                        ) : (
                          <span>
                            {p.job_title || t("communication.noJobTitle")}
                          </span>
                        ),
                    },
                    ...(isAdmin
                      ? [
                          {
                            title: t("expertAccess.experts"),
                            width: 240,
                            render: (_: unknown, p: CommunicationUser) => {
                              const e = employeeFor(p);
                              if (!e) return "—";
                              if (
                                e.membership_status &&
                                e.membership_status !== "current"
                              )
                                return (
                                  <Tag color="gold">
                                    {t("employeeResources.membershipPending")}
                                  </Tag>
                                );
                              if (e.mode === "legacy")
                                return <Tag>{t("expertAccess.legacy")}</Tag>;
                              if (!e.expert_ids.length)
                                return (
                                  <span className={styles.waiting}>
                                    {t("employeeResources.noExpertAssigned")}
                                  </span>
                                );
                              return (
                                <Space size={[3, 4]} wrap>
                                  {e.expert_ids.slice(0, 2).map((id) => (
                                    <Tag className={styles.expertChip} key={id}>
                                      {data?.experts.find(
                                        (e) => e.agent_id === id,
                                      )?.name ?? t("expertAccess.unavailable")}
                                    </Tag>
                                  ))}
                                  {e.expert_ids.length > 2 && (
                                    <Tooltip
                                      title={e.expert_ids
                                        .slice(2)
                                        .map(
                                          (id) =>
                                            data?.experts.find(
                                              (e) => e.agent_id === id,
                                            )?.name ?? id,
                                        )
                                        .join("、")}
                                    >
                                      <Tag>+{e.expert_ids.length - 2}</Tag>
                                    </Tooltip>
                                  )}
                                </Space>
                              );
                            },
                          },
                          {
                            title: t("employeeResources.skillPermissions"),
                            width: 170,
                            render: (_: unknown, p: CommunicationUser) => {
                              const e = employeeFor(p);
                              if (!e || !data) return "—";
                              if (
                                e.membership_status &&
                                e.membership_status !== "current"
                              )
                                return (
                                  <Tag color="gold">
                                    {t("employeeResources.membershipPending")}
                                  </Tag>
                                );
                              if (!assignments.catalogReady)
                                return (
                                  <span className={styles.hint}>
                                    {t("employeeResources.catalogPending")}
                                  </span>
                                );
                              if (e.mode === "legacy")
                                return (
                                  <span className={styles.hint}>
                                    {t("expertAccess.legacy")}
                                  </span>
                                );
                              const skills = effectiveSkills(
                                data,
                                e.expert_ids,
                                e.skill_ids ?? [],
                              );
                              return (
                                <button
                                  className={styles.skillSummary}
                                  onClick={() => openEmployee(p, "skills")}
                                >
                                  <span>
                                    <Puzzle size={16} />
                                    {t("employeeResources.skillTotal", {
                                      count: skills.total,
                                    })}
                                  </span>
                                  <small>
                                    {t("employeeResources.breakdown", {
                                      defaults: skills.defaults.length,
                                      provided: skills.provided.length,
                                      extra: skills.extra.length,
                                    })}
                                  </small>
                                </button>
                              );
                            },
                          },
                          {
                            title: t("communication.actions"),
                            width: 210,
                            render: (_: unknown, p: CommunicationUser) => (
                              <Space size={6}>
                                <Button
                                  size="small"
                                  type="link"
                                  disabled={
                                    preparing ||
                                    !assignments.catalogReady ||
                                    !employeeFor(p) ||
                                    busy
                                  }
                                  onClick={() => openEmployee(p, "experts")}
                                >
                                  {t("employeeResources.expertManagement")}
                                </Button>
                                <Button
                                  size="small"
                                  type="link"
                                  className={styles.skillAction}
                                  disabled={
                                    preparing ||
                                    !assignments.catalogReady ||
                                    !employeeFor(p) ||
                                    busy
                                  }
                                  onClick={() => openEmployee(p, "skills")}
                                >
                                  {t("employeeResources.skillManagement")}
                                </Button>
                                <Tooltip
                                  title={t("employeeResources.tabs.basic")}
                                >
                                  <Button
                                    type="text"
                                    size="small"
                                    aria-label={`${p.nickname} ${t(
                                      "employeeResources.tabs.basic",
                                    )}`}
                                    icon={<SlidersHorizontal size={15} />}
                                    disabled={
                                      preparing ||
                                      !assignments.catalogReady ||
                                      !employeeFor(p) ||
                                      busy
                                    }
                                    onClick={() => openEmployee(p, "basic")}
                                  />
                                </Tooltip>
                              </Space>
                            ),
                          },
                        ]
                      : []),
                  ]}
                />
                <div className={styles.groupFooter}>
                  <span>
                    {t("employeeResources.groupSummary", {
                      count: members.length,
                      assigned: members.filter(
                        (p) => (employeeFor(p)?.expert_ids.length ?? 0) > 0,
                      ).length,
                    })}
                  </span>
                  <span>{t("employeeResources.poolDoesNotAssign")}</span>
                </div>
              </>
            ),
          };
        })}
      />
      <p className={styles.pageFootnote}>
        <LockKeyhole size={13} />
        {t("employeeResources.pageFootnote")}
      </p>
      {editor && data && (
        <EmployeeResourceDrawer
          key={editor.employee.id}
          employee={editor.employee}
          person={editor.person}
          departments={departments}
          jobTitles={titles}
          initialTab={editor.tab}
          data={data}
          close={() => setEditor(undefined)}
          onSaved={refresh}
          onManagePool={(scope, kind) => {
            setEditor(undefined);
            setPicker({ scope, kind });
          }}
        />
      )}
      {picker && data && (
        <ResourcePicker
          key={`${picker.scope}:${picker.kind}`}
          scopeId={picker.scope}
          scopeName={scopeName(picker.scope)}
          kind={picker.kind}
          data={data}
          close={() => setPicker(undefined)}
          onSaved={assignments.reload}
        />
      )}
      {batch && data && (
        <BatchResourceDrawer
          kind={batch}
          employees={selectedEmployees}
          people={users}
          departments={departments}
          data={data}
          close={() => {
            setBatch(undefined);
            setSelected([]);
          }}
          onSaved={refresh}
        />
      )}
      <Modal
        open={editing !== undefined}
        title={t(
          editing
            ? "communication.editDepartment"
            : "communication.createDepartment",
        )}
        onCancel={() => setEditing(undefined)}
        onOk={() => void saveDepartment().catch(() => undefined)}
        confirmLoading={busy}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label={t("communication.name")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={80} />
          </Form.Item>
          <Form.Item
            name="parent_id"
            label={t("communication.parentDepartment")}
          >
            <Select
              allowClear
              options={departments
                .filter((d) => !descendants.has(d.department_id))
                .map((d) => ({ value: d.department_id, label: d.name }))}
            />
          </Form.Item>
          <Form.Item name="sort_order" label={t("communication.sortOrder")}>
            <InputNumber min={0} max={100000} />
          </Form.Item>
          <Form.Item
            name="enabled"
            label={t("communication.enabled")}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
        </Form>
        {editing && (
          <Button
            disabled={!editing.enabled || busy}
            loading={openingGroup === editing.department_id}
            onClick={() => void openGroup(editing)}
          >
            {t(
              editing.conversation_id
                ? "communication.manageGroup"
                : "communication.createGroup",
            )}
          </Button>
        )}
      </Modal>
      <Modal
        open={companySettings}
        title={t("employeeResources.companySettings")}
        onCancel={() => setCompanySettings(false)}
        confirmLoading={busy}
        onOk={() =>
          void run(() =>
            org.saveDirectorySettings(name, controller.current.signal),
          )
            .then(() => setCompanySettings(false))
            .catch(() => undefined)
        }
      >
        <label htmlFor="company-directory-name">
          {t("communication.directoryName")}
        </label>
        <Input
          id="company-directory-name"
          maxLength={80}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <p className={styles.hint}>{t("communication.directoryNameHint")}</p>
      </Modal>
      <Drawer
        open={installed}
        width={600}
        title={t("employeeResources.installedResources")}
        onClose={() => setInstalled(false)}
      >
        <p className={styles.hint}>{t("employeeResources.installedHint")}</p>
        <Tabs
          items={[
            {
              key: "experts",
              label: t("employeeResources.tabs.experts"),
              children: (
                <>
                  <Button type="link" onClick={() => navigate("/experts")}>
                    {t("employeeResources.goExperts")}
                  </Button>
                  <div className={styles.candidates}>
                    {data?.experts.map((e) => (
                      <div className={styles.candidate} key={e.agent_id}>
                        <GraduationCap size={18} />
                        <div>
                          <strong>{e.name}</strong>
                          <p className={styles.hint}>{e.description}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ),
            },
            {
              key: "skills",
              label: t("employeeResources.tabs.skills"),
              children: (
                <>
                  <Button
                    type="link"
                    onClick={() => navigate("/skill-packages")}
                  >
                    {t("employeeResources.goSkills")}
                  </Button>
                  <Button
                    type="link"
                    onClick={() => {
                      setInstalled(false);
                      setPicker({ scope: "unassigned", kind: "defaults" });
                    }}
                  >
                    {t("employeeResources.configureDefaults")}
                  </Button>
                  <div className={styles.candidates}>
                    {data?.skills?.map((s) => (
                      <div className={styles.candidate} key={s.id}>
                        <Puzzle size={17} />
                        <div>
                          <strong>{s.name}</strong>
                          <p className={styles.hint}>{s.description}</p>
                          <small className={styles.hint}>
                            {s.source_name === "builtin"
                              ? t("employeeResources.builtin")
                              : s.source_name}
                          </small>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ),
            },
          ]}
        />
      </Drawer>
      {group && renderGroup(group, () => setGroup(""))}
    </div>
  );
}
