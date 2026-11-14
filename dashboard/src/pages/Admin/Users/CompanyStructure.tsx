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
  Tree,
} from "antd";
import type { DataNode } from "antd/es/tree";
import {
  ChevronDown,
  GraduationCap,
  Inbox,
  Info,
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
import { departmentPath } from "../../Contacts/directoryTree";
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
import CompanyGroupDirectory from "./CompanyGroupDirectory";
import styles from "./expertAssignments.module.less";

const org = communicationApi.organization;
type DepartmentForm = Pick<
  Department,
  "name" | "parent_id" | "sort_order" | "enabled"
>;
/** Show the department filter only when the tree no longer fits at a glance. */
const TREE_SEARCH_THRESHOLD = 8;

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
  const [treeSearch, setTreeSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [current, setCurrent] = useState<string>();
  const [treeExpanded, setTreeExpanded] = useState<string[]>();
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
  const [groupDirectory, setGroupDirectory] = useState(false);
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
    setCurrent(undefined);
    setTreeExpanded(undefined);
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
  const headcount = (id: string) =>
    users.filter((p) => scopeFor(p) === id).length;
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
  // Selection falls back to the first populated scope, so a deleted or moved
  // department never leaves the detail pane empty.
  const scope =
    scopes.find((s) => s.id === current) ??
    scopes.find((s) => users.some((p) => scopeFor(p) === s.id)) ??
    scopes[0];
  const searching = !!search;
  const members = searching
    ? filtered
    : filtered.filter((p) => scopeFor(p) === scope.id);
  const pool = poolFor(scope.id);
  const selectScope = (id: string) => {
    setCurrent(id);
    setSearch("");
    setSelected([]);
  };
  const departmentIds = new Set(departments.map((d) => d.department_id));
  const sortedDepartments = [...departments].sort(
    (a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name),
  );
  const childrenOf = (parent: string | null) =>
    sortedDepartments.filter((d) =>
      parent === null
        ? !d.parent_id ||
          d.parent_id === d.department_id ||
          !departmentIds.has(d.parent_id)
        : d.parent_id === parent && d.department_id !== parent,
    );
  const treeQuery = treeSearch.trim().toLocaleLowerCase();
  const treeTitle = (
    id: string,
    label: string,
    options: { disabled?: boolean; icon?: ReactNode; warn?: boolean } = {},
  ) => {
    const count = headcount(id);
    return (
      <span
        className={`${styles.treeNode} ${
          options.disabled ? styles.treeNodeDisabled : ""
        }`}
      >
        {options.icon}
        <span className={styles.treeName}>{label}</span>
        {options.disabled && (
          <Tag className={styles.treeTag}>{t("communication.disabled")}</Tag>
        )}
        <span
          className={`${styles.treeCount} ${
            options.warn && count ? styles.treeCountWarn : ""
          }`}
        >
          {count || ""}
        </span>
      </span>
    );
  };
  const branch = (parent: string | null, seen: Set<string>): DataNode[] =>
    childrenOf(parent).flatMap((d) => {
      if (seen.has(d.department_id)) return [];
      const next = new Set(seen).add(d.department_id);
      const children = branch(d.department_id, next);
      if (
        treeQuery &&
        !children.length &&
        !d.name.toLocaleLowerCase().includes(treeQuery)
      )
        return [];
      return [
        {
          key: d.department_id,
          title: treeTitle(d.department_id, d.name, {
            disabled: !d.enabled,
          }),
          children: children.length ? children : undefined,
        },
      ];
    });
  const treeData: DataNode[] = [
    ...branch(null, new Set()),
    ...scopes
      .filter((s) => !s.department && s.id !== "unassigned")
      .filter(
        (s) => !treeQuery || s.name.toLocaleLowerCase().includes(treeQuery),
      )
      .map((s) => ({ key: s.id, title: treeTitle(s.id, s.name) })),
  ];
  const parents = departments
    .filter((d) => departments.some((c) => c.parent_id === d.department_id))
    .map((d) => d.department_id);
  const expandedKeys = treeQuery ? parents : treeExpanded ?? parents;
  const path = scope.department
    ? departmentPath(scope.department.parent_id, departments)
    : [];
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
  const employeeDisabled = (p: CommunicationUser) =>
    preparing || !assignments.catalogReady || !employeeFor(p);
  const resourceName = (kind: "experts" | "skills", id: string) =>
    (kind === "experts"
      ? data?.experts.find((e) => e.agent_id === id)?.name
      : data?.skills?.find((s) => s.id === id)?.name) ??
    t("expertAccess.unavailable");
  return (
    <div className={styles.structure}>
      <div className={styles.sectionHeading}>
        <p>{t("employeeResources.structureDescription")}</p>
        <Space wrap>
          <Button onClick={() => setGroupDirectory(true)}>
            {t("communication.manageGroups")}
          </Button>
          <Button
            icon={<Settings2 size={15} />}
            onClick={() => setCompanySettings(true)}
          >
            {t("employeeResources.companySettings")}
          </Button>
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
          className={styles.notice}
        />
      )}
      {isAdmin && data && data.source.source_user_id == null && (
        <Alert
          type="warning"
          showIcon
          message={t("employeeResources.sourceMissing")}
          action={
            <Button size="small" onClick={() => setCompanySettings(true)}>
              {t("employeeResources.openCompanySettings")}
            </Button>
          }
          className={styles.notice}
        />
      )}
      <div className={styles.layout}>
        <aside
          className={styles.treePane}
          aria-label={t("communication.companyStructure")}
        >
          {scopes.length > TREE_SEARCH_THRESHOLD && (
            <Input
              size="small"
              prefix={<Search size={13} />}
              aria-label={t("employeeResources.searchDepartments")}
              placeholder={t("employeeResources.searchDepartments")}
              value={treeSearch}
              onChange={(e) => setTreeSearch(e.target.value)}
              allowClear
              className={styles.treeSearch}
            />
          )}
          <Tree
            blockNode
            className={styles.tree}
            treeData={treeData}
            expandedKeys={expandedKeys}
            onExpand={(keys) => setTreeExpanded(keys.map(String))}
            selectedKeys={searching ? [] : [scope.id]}
            onSelect={(keys, info) =>
              selectScope(String(keys[0] ?? info.node.key))
            }
          />
          <Tree
            blockNode
            className={`${styles.tree} ${styles.unassignedTree}`}
            treeData={[
              {
                key: "unassigned",
                title: treeTitle("unassigned", t("communication.unassigned"), {
                  icon: <Inbox size={14} />,
                  warn: true,
                }),
              },
            ]}
            selectedKeys={
              !searching && scope.id === "unassigned" ? ["unassigned"] : []
            }
            onSelect={() => selectScope("unassigned")}
          />
        </aside>
        <section className={styles.detail}>
          <div className={styles.detailHeading}>
            <div>
              {searching ? (
                <h3>
                  {t("employeeResources.searchResults", {
                    count: members.length,
                  })}
                </h3>
              ) : (
                <>
                  {!!path.length && (
                    <span className={styles.breadcrumb}>
                      {path.map((d) => d.name).join(" / ")} /
                    </span>
                  )}
                  <h3>
                    {scope.name}
                    {scope.department && !scope.department.enabled && (
                      <Tag>{t("communication.disabled")}</Tag>
                    )}
                  </h3>
                  <span className={styles.detailMeta}>
                    {t("employeeResources.groupSummary", {
                      count: members.length,
                      assigned: members.filter(
                        (p) => (employeeFor(p)?.expert_ids.length ?? 0) > 0,
                      ).length,
                    })}
                    {scope.id === "unassigned" &&
                      ` · ${t("employeeResources.unassignedHint")}`}
                  </span>
                </>
              )}
            </div>
            {!searching && scope.department && (
              <Space wrap>
                <Button
                  disabled={busy || !scope.department.enabled}
                  loading={openingGroup === scope.id}
                  onClick={() => void openGroup(scope.department!)}
                >
                  {t(
                    scope.department.conversation_id
                      ? "communication.manageGroup"
                      : "communication.createGroup",
                  )}
                </Button>
                <Button
                  icon={<SlidersHorizontal size={14} />}
                  onClick={() => edit(scope.department!)}
                >
                  {t("employeeResources.departmentSettings")}
                </Button>
              </Space>
            )}
          </div>
          {isAdmin && data && !searching && (
            <div className={styles.departmentPool}>
              {(["experts", "skills"] as const).map((kind) => {
                const ids =
                  kind === "experts"
                    ? pool?.expert_ids ?? []
                    : pool?.skill_ids ?? [];
                const label = t(
                  kind === "experts"
                    ? "employeeResources.departmentExperts"
                    : "employeeResources.departmentSkills",
                );
                const total = t(
                  kind === "experts"
                    ? "employeeResources.expertTotal"
                    : "employeeResources.skillTotal",
                  { count: ids.length },
                );
                const tags = ids.map((id) => (
                  <Tag
                    className={
                      kind === "experts" ? styles.expertChip : styles.skillChip
                    }
                    key={id}
                  >
                    {resourceName(kind, id)}
                  </Tag>
                ));
                return (
                  <div key={kind} className={styles.poolRow}>
                    <span className={styles.poolLabel}>
                      {kind === "experts" ? (
                        <ShieldCheck size={14} />
                      ) : (
                        <Puzzle size={14} />
                      )}
                      {label}
                      <Tooltip
                        title={t(
                          kind === "experts"
                            ? "employeeResources.poolExpertNote"
                            : "employeeResources.poolSkillNote",
                        )}
                      >
                        <Info size={13} className={styles.poolInfo} />
                      </Tooltip>
                    </span>
                    <div className={styles.poolContent}>
                      {ids.length > 1 ? (
                        <details
                          key={scope.id}
                          className={styles.poolResources}
                        >
                          <summary
                            className={styles.poolResourcesSummary}
                            aria-label={`${label} · ${total}`}
                          >
                            <span className={styles.poolResourcesPreview}>
                              {ids
                                .map((id) => resourceName(kind, id))
                                .join(" · ")}
                            </span>
                            <span className={styles.poolResourcesCount}>
                              {total}
                              <ChevronDown size={14} aria-hidden="true" />
                            </span>
                          </summary>
                          <div className={styles.poolResourcesList}>{tags}</div>
                        </details>
                      ) : (
                        tags
                      )}
                      {!ids.length && (
                        <span className={styles.hint}>
                          {t("employeeResources.noDepartmentResources")}
                        </span>
                      )}
                    </div>
                    <Button
                      type="link"
                      size="small"
                      icon={<Plus size={14} />}
                      disabled={!assignments.catalogReady}
                      onClick={() => setPicker({ scope: scope.id, kind })}
                    >
                      {t(
                        kind === "experts"
                          ? "employeeResources.manageExperts"
                          : "employeeResources.manageSkills",
                      )}
                    </Button>
                  </div>
                );
              })}
              <p className={styles.poolFootnote}>
                {t("employeeResources.poolDoesNotAssign")}
              </p>
            </div>
          )}
          <div className={styles.peopleToolbar}>
            <Input
              prefix={<Search size={14} />}
              aria-label={t("employeeResources.searchPeople")}
              placeholder={t("employeeResources.searchPeople")}
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setSelected([]);
              }}
              allowClear
              className={styles.peopleSearch}
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
            <Tooltip title={t("communication.refresh")}>
              <Button
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
          </div>
          {!!selected.length && (
            <div className={styles.batchBar}>
              <span>
                {t("employeeResources.batchCount", { count: selected.length })}
              </span>
              {(["experts", "skills", "department"] as const).map((kind) => (
                <Button
                  key={kind}
                  size="small"
                  onClick={() => void openBatch(kind)}
                  disabled={
                    preparing ||
                    !assignments.catalogReady ||
                    selectedEmployees.length !== selected.length
                  }
                >
                  {t(`employeeResources.batch.${kind}`)}
                </Button>
              ))}
              <Button size="small" type="text" onClick={() => setSelected([])}>
                {t("expertAccess.clear")}
              </Button>
            </div>
          )}
          <Table<CommunicationUser>
            className={styles.employeeTable}
            rowKey="user_id"
            size="middle"
            loading={loading}
            dataSource={members}
            scroll={{ x: isAdmin ? (searching ? 900 : 780) : 420 }}
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            rowSelection={
              isAdmin
                ? {
                    selectedRowKeys: selected,
                    onChange: (values) => setSelected(values.map(String)),
                    getCheckboxProps: (person) => ({
                      disabled:
                        employeeDisabled(person) ||
                        (selected.length > 0 &&
                          !selected.includes(person.user_id) &&
                          scopeFor(
                            users.find((p) => p.user_id === selected[0]),
                          ) !== scopeFor(person)),
                    }),
                  }
                : undefined
            }
            columns={[
              {
                title: t("employeeResources.employee"),
                width: 200,
                render: (_, p) => (
                  <div className={styles.employeeIdentity}>
                    <Avatar shape="square" size={32}>
                      {p.nickname.slice(0, 2)}
                    </Avatar>
                    <div>
                      <strong>
                        {p.nickname}
                        {p.status !== "active" && (
                          <Tag className={styles.statusTag}>
                            {t("communication.disabled")}
                          </Tag>
                        )}
                      </strong>
                      <small>
                        {employeeFor(p)?.username ??
                          p.user_id.split(":").at(-1)}
                      </small>
                    </div>
                  </div>
                ),
              },
              ...(searching
                ? [
                    {
                      title: t("communication.department"),
                      width: 130,
                      render: (_: unknown, p: CommunicationUser) => (
                        <button
                          className={styles.linkText}
                          onClick={() => selectScope(scopeFor(p))}
                        >
                          {scopeName(scopeFor(p))}
                        </button>
                      ),
                    },
                  ]
                : []),
              {
                title: t("communication.jobTitle"),
                width: 130,
                render: (_, p) =>
                  isAdmin ? (
                    <Button
                      type="text"
                      size="small"
                      className={styles.positionButton}
                      disabled={employeeDisabled(p)}
                      onClick={() => openEmployee(p, "basic")}
                    >
                      {p.job_title || t("communication.noJobTitle")}
                      <ChevronDown size={12} />
                    </Button>
                  ) : (
                    <span>{p.job_title || t("communication.noJobTitle")}</span>
                  ),
              },
              ...(isAdmin
                ? [
                    {
                      title: t("expertAccess.experts"),
                      width: 220,
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
                          <Space size={[4, 4]} wrap>
                            {e.expert_ids.slice(0, 2).map((id) => (
                              <Tag className={styles.expertChip} key={id}>
                                {resourceName("experts", id)}
                              </Tag>
                            ))}
                            {e.expert_ids.length > 2 && (
                              <Tooltip
                                title={e.expert_ids
                                  .slice(2)
                                  .map(
                                    (id) =>
                                      data?.experts.find(
                                        (x) => x.agent_id === id,
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
                      width: 150,
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
                          <Tooltip
                            title={t("employeeResources.breakdown", {
                              defaults: skills.defaults.length,
                              provided: skills.provided.length,
                              extra: skills.extra.length,
                            })}
                          >
                            <button
                              className={styles.skillSummary}
                              onClick={() => openEmployee(p, "skills")}
                            >
                              <Puzzle size={14} />
                              {t("employeeResources.skillTotal", {
                                count: skills.total,
                              })}
                            </button>
                          </Tooltip>
                        );
                      },
                    },
                    {
                      title: t("communication.actions"),
                      width: 80,
                      fixed: "right" as const,
                      render: (_: unknown, p: CommunicationUser) => (
                        <Button
                          size="small"
                          aria-label={`${p.nickname} ${t("common.edit")}`}
                          disabled={employeeDisabled(p) || busy}
                          onClick={() => openEmployee(p, "experts")}
                        >
                          {t("common.edit")}
                        </Button>
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </section>
      </div>
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
        width={600}
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
        {isAdmin && data && (
          <div className={styles.settingsSource}>
            <ExpertAssignmentSource
              data={data}
              onSaved={assignments.reload}
              onInstalled={() => {
                setCompanySettings(false);
                setInstalled(true);
              }}
              catalogReady={assignments.catalogReady}
            />
            <p className={styles.hint}>{t("expertAccess.sourceHint")}</p>
          </div>
        )}
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
      {groupDirectory && (
        <CompanyGroupDirectory
          close={() => setGroupDirectory(false)}
          manage={setGroup}
        />
      )}
      {group && renderGroup(group, () => setGroup(""))}
    </div>
  );
}
