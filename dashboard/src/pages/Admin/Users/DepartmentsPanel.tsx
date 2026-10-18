import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  App,
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
  Tag,
} from "antd";
import { Plus, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import {
  communicationApi,
  communicationError,
} from "../../../api/modules/communication";
import type {
  CommunicationUser,
  Department,
  ManagedGroup,
  JobTitle,
} from "../../../api/types/communication";
import { newClientMessageId } from "../../../utils/communicationId";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { communicationErrorText } from "../../Messaging/communicationUi";
import { useCommunication } from "../../Messaging/hooks/CommunicationContext";

const org = communicationApi.organization;
type DepartmentForm = Pick<
  Department,
  "name" | "parent_id" | "sort_order" | "enabled"
>;

function GroupManager({ id, close }: { id: string; close: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { state, client } = useCommunication();
  const joinRequest = useRef(newClientMessageId());
  const [group, setGroup] = useState<ManagedGroup>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form] = Form.useForm<{
    name: string;
    announcement: string;
    mute_all: boolean;
  }>();
  const controller = useRef(new AbortController());
  const isMember = group?.members.some(
    (member) => member.user_id === state.profile?.user_id,
  );
  const load = useCallback(async () => {
    const signal = controller.current.signal;
    const value = await org.group(id, signal);
    if (!signal.aborted) {
      setGroup(value);
      form.setFieldsValue({
        name: value.name,
        announcement: value.announcement ?? "",
        mute_all: value.mute_all,
      });
    }
  }, [id, form]);
  useEffect(() => {
    controller.current = new AbortController();
    const current = controller.current;
    void load().catch((e) => {
      if (!current.signal.aborted) setError(communicationErrorText(e, t));
    });
    return () => current.abort();
  }, [load, t]);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch (e) {
      if (!controller.current.signal.aborted)
        setError(communicationErrorText(e, t));
    } finally {
      if (!controller.current.signal.aborted) setBusy(false);
    }
  };
  return (
    <Drawer
      open
      onClose={close}
      title={group?.name || t("communication.groupDetails")}
      width={700}
    >
      {error && <Alert type="error" showIcon message={error} />}
      {group && state.profile && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={t(
            isMember
              ? "communication.groupMemberEntryHint"
              : "communication.groupManagementOnlyHint",
          )}
          action={
            isMember ? (
              <Button
                onClick={() => {
                  close();
                  navigate(`/messages/${encodeURIComponent(id)}`);
                }}
              >
                {t("communication.openGroupChat")}
              </Button>
            ) : (
              <Button
                loading={busy}
                onClick={() =>
                  void run(async () => {
                    await communicationApi.groupMembers(
                      id,
                      {
                        request_id: joinRequest.current,
                        user_ids: [state.profile!.user_id],
                      },
                      false,
                      true,
                    );
                    await client.synchronize();
                  })
                }
              >
                {t("communication.joinGroup")}
              </Button>
            )
          }
        />
      )}
      <p>{t("communication.compulsoryGroup")}</p>
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) =>
          void run(() => org.updateGroup(id, values, controller.current.signal))
        }
      >
        <Form.Item
          name="name"
          label={t("communication.name")}
          rules={[{ required: true, whitespace: true }]}
        >
          <Input maxLength={80} />
        </Form.Item>
        <Form.Item name="announcement" label={t("communication.announcement")}>
          <Input.TextArea maxLength={500} rows={3} />
        </Form.Item>
        <Form.Item
          name="mute_all"
          label={t("communication.muteAll")}
          valuePropName="checked"
        >
          <Switch disabled={busy} />
        </Form.Item>
        <Button
          htmlType="submit"
          type="primary"
          loading={busy}
          disabled={!group}
        >
          {t("communication.save")}
        </Button>
      </Form>
      <h3>{t("communication.members")}</h3>
      <Table
        size="small"
        rowKey="user_id"
        dataSource={group?.members ?? []}
        loading={!group && !error}
        scroll={{ x: 550 }}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: t("communication.name"), dataIndex: "nickname" },
          {
            title: t("communication.membershipSource"),
            render: (_, member) => (
              <Tag>
                {t(
                  `communication.memberSources.${
                    member.membership_source || "automatic"
                  }`,
                )}
              </Tag>
            ),
          },
          {
            title: t("communication.role"),
            render: (_, member) =>
              member.role === "owner" ? (
                <Tag>{t("communication.roles.owner")}</Tag>
              ) : (
                <Select
                  size="small"
                  value={member.role}
                  disabled={busy}
                  options={[
                    { value: "member", label: t("communication.roles.member") },
                    { value: "admin", label: t("communication.roles.admin") },
                  ]}
                  onChange={(role) =>
                    void run(() =>
                      org.member(
                        id,
                        member.user_id,
                        { role },
                        controller.current.signal,
                      ),
                    )
                  }
                />
              ),
          },
          {
            title: t("communication.actions"),
            render: (_, member) => {
              const muted =
                !!member.mute_until &&
                Date.parse(member.mute_until) > Date.now();
              return member.role !== "owner" ? (
                <Space>
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        org.member(
                          id,
                          member.user_id,
                          {
                            mute_until: muted
                              ? null
                              : new Date(Date.now() + 3600000).toISOString(),
                          },
                          controller.current.signal,
                        ),
                      )
                    }
                  >
                    {t(
                      muted
                        ? "communication.unmuteMember"
                        : "communication.muteHour",
                    )}
                  </Button>
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() =>
                      Modal.confirm({
                        title: t("communication.transferOwner"),
                        content: member.nickname,
                        okText: t("communication.confirm"),
                        cancelText: t("communication.cancel"),
                        onOk: () =>
                          run(() =>
                            org.owner(
                              id,
                              member.user_id,
                              controller.current.signal,
                            ),
                          ),
                      })
                    }
                  >
                    {t("communication.transferOwner")}
                  </Button>
                </Space>
              ) : null;
            },
          },
        ]}
      />
    </Drawer>
  );
}

export default function DepartmentsPanel() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const user = useCurrentUser();
  const { client } = useCommunication();
  const [directoryName, setDirectoryName] = useState("");
  const [departments, setDepartments] = useState<Department[]>([]);
  const [users, setUsers] = useState<CommunicationUser[]>([]);
  const [jobTitles, setJobTitles] = useState<JobTitle[]>([]);
  const [addingPositionFor, setAddingPositionFor] = useState<string>();
  const [newPosition, setNewPosition] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Department | null | undefined>();
  const [group, setGroup] = useState("");
  const [openingGroup, setOpeningGroup] = useState("");
  const [search, setSearch] = useState("");
  const [form] = Form.useForm<DepartmentForm>();
  const controller = useRef(new AbortController());
  const load = useCallback(async () => {
    const signal = controller.current.signal;
    setLoading(true);
    try {
      const [depts, people, settings, titles] = await Promise.all([
        org.departments(signal),
        org.users(signal),
        org.directorySettings(signal),
        org.jobTitles(signal),
      ]);
      if (!signal.aborted) {
        setDepartments(depts.items);
        setUsers(people.items);
        setJobTitles(titles.items);
        setDirectoryName(settings.name);
        setError("");
      }
    } finally {
      if (!signal.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    controller.current = new AbortController();
    const current = controller.current;
    setDepartments([]);
    setUsers([]);
    setGroup("");
    setEditing(undefined);
    void load().catch((e) => {
      if (!current.signal.aborted) setError(communicationErrorText(e, t));
    });
    return () => current.abort();
  }, [load, user?.id, t]);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    const signal = controller.current.signal;
    try {
      await action();
      if (!signal.aborted) await load();
      if (!signal.aborted) await client.synchronize({ throwOnError: true });
    } catch (e) {
      if (!signal.aborted) setError(communicationErrorText(e, t));
      throw e;
    } finally {
      if (!signal.aborted) setBusy(false);
    }
  };
  const edit = (department: Department | null) => {
    form.setFieldsValue(
      department ?? { name: "", parent_id: null, sort_order: 0, enabled: true },
    );
    setEditing(department);
  };
  const save = async () => {
    const fields = await form.validateFields();
    const values = { ...fields, parent_id: fields.parent_id ?? null };
    await run(() =>
      editing
        ? org.update(editing.department_id, values, controller.current.signal)
        : org.create(values, controller.current.signal),
    );
    setEditing(undefined);
  };
  const openDepartmentGroup = async (department: Department) => {
    const signal = controller.current.signal;
    let opened = false;
    setOpeningGroup(department.department_id);
    try {
      await run(async () => {
        const value = department.conversation_id
          ? { conversation_id: department.conversation_id }
          : await org.createGroup(department.department_id, signal);
        if (signal.aborted) return;
        opened = true;
        setGroup(value.conversation_id);
        if (!department.conversation_id)
          void message.success(t("communication.departmentGroupCreated"));
      });
    } catch (e) {
      if (signal.aborted) return;
      const detail = opened
        ? t("communication.groupRefreshFailed")
        : communicationError(e).code === "invalid_state"
        ? t("communication.departmentGroupNeedsMembers")
        : communicationErrorText(e, t);
      setError(detail);
      if (opened) void message.warning(detail);
      else void message.error(detail);
    } finally {
      if (!signal.aborted) setOpeningGroup("");
    }
  };
  const descendants = new Set<string>();
  if (editing) {
    descendants.add(editing.department_id);
    let changed = true;
    while (changed) {
      changed = false;
      for (const dept of departments)
        if (
          dept.parent_id &&
          descendants.has(dept.parent_id) &&
          !descendants.has(dept.department_id)
        ) {
          descendants.add(dept.department_id);
          changed = true;
        }
    }
  }
  return (
    <div>
      <div style={{ marginBottom: 24 }}>
        <label
          htmlFor="company-directory-name"
          style={{ display: "block", fontWeight: 600, marginBottom: 8 }}
        >
          {t("communication.directoryName")}
        </label>
        <Space.Compact style={{ width: "min(100%, 460px)" }}>
          <Input
            id="company-directory-name"
            value={directoryName}
            maxLength={80}
            placeholder={t("communication.contacts")}
            disabled={loading || busy}
            onChange={(event) => setDirectoryName(event.target.value)}
          />
          <Button
            loading={busy}
            disabled={loading}
            onClick={() =>
              void run(async () => {
                await org.saveDirectorySettings(
                  directoryName,
                  controller.current.signal,
                );
                await client.synchronize();
              }).catch(() => undefined)
            }
          >
            {t("communication.save")}
          </Button>
        </Space.Compact>
        <p style={{ color: "var(--fn-text-tertiary)", marginTop: 8 }}>
          {t("communication.directoryNameHint")}
        </p>
      </div>
      <p>{t("communication.departmentsHint")}</p>
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          style={{ marginBottom: 16 }}
        />
      )}
      <Space wrap style={{ marginBottom: 16 }}>
        <Button
          type="primary"
          icon={<Plus size={15} />}
          onClick={() => edit(null)}
        >
          {t("communication.createDepartment")}
        </Button>
        <Button
          icon={<RefreshCw size={14} />}
          loading={loading}
          onClick={() =>
            void load().catch((e) => setError(communicationErrorText(e, t)))
          }
        >
          {t("communication.refresh")}
        </Button>
      </Space>
      <Table
        rowKey="department_id"
        dataSource={departments}
        loading={loading}
        pagination={false}
        scroll={{ x: 600 }}
        columns={[
          { title: t("communication.name"), dataIndex: "name" },
          {
            title: t("communication.people"),
            dataIndex: "member_count",
            render: (value: number | undefined) => value ?? 0,
          },
          {
            title: t("communication.parentDepartment"),
            render: (_, dept) =>
              departments.find((d) => d.department_id === dept.parent_id)
                ?.name || "—",
          },
          {
            title: t("communication.enabled"),
            render: (_, dept) => (
              <Switch
                size="small"
                checked={dept.enabled}
                disabled={busy}
                onChange={(enabled) =>
                  void run(() =>
                    org.update(
                      dept.department_id,
                      { enabled },
                      controller.current.signal,
                    ),
                  ).catch(() => undefined)
                }
              />
            ),
          },
          {
            title: t("communication.actions"),
            render: (_, dept) => (
              <Space>
                <Button size="small" disabled={busy} onClick={() => edit(dept)}>
                  {t("communication.edit")}
                </Button>
                <Button
                  size="small"
                  disabled={busy || !dept.enabled}
                  loading={openingGroup === dept.department_id}
                  onClick={() => void openDepartmentGroup(dept)}
                >
                  {t(
                    dept.conversation_id
                      ? "communication.manageGroup"
                      : "communication.createGroup",
                  )}
                </Button>
              </Space>
            ),
          },
        ]}
      />
      <h3 style={{ marginTop: 24 }}>{t("communication.assignPeople")}</h3>
      <Input.Search
        value={search}
        allowClear
        onChange={(e) => setSearch(e.target.value)}
        placeholder={t("communication.searchPeople")}
        style={{ maxWidth: 320, marginBottom: 12 }}
      />
      <Table
        rowKey="user_id"
        dataSource={users.filter(
          (person) =>
            !person.is_admin &&
            `${person.nickname} ${person.job_title ?? ""}`
              .toLowerCase()
              .includes(search.toLowerCase()),
        )}
        loading={loading}
        scroll={{ x: 500 }}
        columns={[
          { title: t("communication.name"), dataIndex: "nickname" },
          {
            title: t("communication.status"),
            render: (_, person) => (
              <Tag>
                {t(
                  person.status === "active"
                    ? "communication.active"
                    : "communication.disabled",
                )}
              </Tag>
            ),
          },
          {
            title: t("communication.jobTitle"),
            render: (_, person) => (
              <Select
                style={{ minWidth: 160 }}
                showSearch
                optionFilterProp="label"
                allowClear
                disabled={busy}
                value={person.job_title_id ?? undefined}
                placeholder={t("communication.noJobTitle")}
                options={jobTitles.map((job) => ({
                  value: job.job_title_id,
                  label: job.name,
                }))}
                onChange={(value) =>
                  void run(() =>
                    org.assignJobTitle(
                      person.user_id,
                      value ?? null,
                      controller.current.signal,
                    ),
                  ).catch(() => undefined)
                }
                popupRender={(menu) => (
                  <>
                    {menu}
                    <Button
                      type="text"
                      block
                      icon={<Plus size={14} />}
                      onClick={() => {
                        setNewPosition("");
                        setAddingPositionFor(person.user_id);
                      }}
                    >
                      {t("communication.addJobTitle")}
                    </Button>
                  </>
                )}
              />
            ),
          },
          {
            title: t("communication.department"),
            render: (_, person) => (
              <Select
                style={{ minWidth: 180 }}
                allowClear
                disabled={busy}
                value={person.department_id ?? undefined}
                placeholder={t("communication.unassigned")}
                options={departments
                  .filter((d) => d.enabled)
                  .map((d) => ({ value: d.department_id, label: d.name }))}
                onChange={(value) =>
                  void run(() =>
                    org.assign(
                      person.user_id,
                      value ?? null,
                      controller.current.signal,
                    ),
                  ).catch(() => undefined)
                }
              />
            ),
          },
        ]}
      />
      <p style={{ color: "var(--fn-text-tertiary)" }}>
        {t("communication.jobTitleHint")}
      </p>
      <Modal
        open={!!addingPositionFor}
        title={t("communication.addJobTitle")}
        okText={t("communication.save")}
        cancelText={t("communication.cancel")}
        confirmLoading={busy}
        okButtonProps={{ disabled: !newPosition.trim() }}
        onCancel={() => {
          if (!busy) setAddingPositionFor(undefined);
        }}
        onOk={async () => {
          if (!addingPositionFor || !newPosition.trim()) return;
          await run(async () => {
            const title = await org.createJobTitle(
              newPosition.trim(),
              controller.current.signal,
            );
            await org.assignJobTitle(
              addingPositionFor,
              title.job_title_id,
              controller.current.signal,
            );
          });
          setAddingPositionFor(undefined);
        }}
      >
        <Input
          autoFocus
          maxLength={40}
          value={newPosition}
          aria-label={t("communication.jobTitle")}
          placeholder={t("communication.jobTitlePlaceholder")}
          onChange={(event) => setNewPosition(event.target.value)}
        />
      </Modal>
      <Modal
        open={editing !== undefined}
        title={t(
          editing
            ? "communication.editDepartment"
            : "communication.createDepartment",
        )}
        onCancel={() => setEditing(undefined)}
        onOk={() => void save().catch(() => undefined)}
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
      </Modal>
      {group && (
        <GroupManager
          key={`${user?.id}:${group}`}
          id={group}
          close={() => setGroup("")}
        />
      )}
    </div>
  );
}
