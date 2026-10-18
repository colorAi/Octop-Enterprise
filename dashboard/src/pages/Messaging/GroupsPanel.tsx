import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Avatar,
  Button,
  Empty,
  Input,
  List,
  Modal,
  Select,
  Space,
  Switch,
  Tag,
} from "antd";
import { Users } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { communicationApi } from "../../api/modules/communication";
import type { GroupInfo, GroupKind } from "../../api/types/communication";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useConversationPageVisible } from "../../hooks/useConversationPageVisible";
import { newClientMessageId } from "../../utils/communicationId";
import { userCan } from "../../utils/permissions";
import { useCommunication } from "./hooks/CommunicationContext";
import {
  communicationErrorText,
  visibleConversations,
} from "./communicationUi";
import GroupDetails from "./GroupDetails";

export default function GroupsPanel() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useCurrentUser();
  const pageVisible = useConversationPageVisible();
  const { client, state } = useCommunication();
  const [kind, setKind] = useState<GroupKind | "all">("all");
  const [search, setSearch] = useState("");
  const [managed, setManaged] = useState(false);
  const [management, setManagement] = useState<GroupInfo[]>([]);
  const [detail, setDetail] = useState<GroupInfo>();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [members, setMembers] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [createdGroup, setCreatedGroup] = useState<string>();
  const request = useRef<{ payload: string; id: string }>();
  const canManage = userCan(user, "departments");
  const canCreate = userCan(user, "groups_create");
  useEffect(() => {
    if (!managed || !pageVisible) return;
    const controller = new AbortController();
    void communicationApi
      .managedGroups(controller.signal)
      .then((v) => {
        if (!controller.signal.aborted) setManagement(v.items);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(communicationErrorText(e, t));
      });
    return () => controller.abort();
  }, [managed, pageVisible, state.sequence, t]);
  const joined: GroupInfo[] = visibleConversations(state, {
    includeHidden: true,
  })
    .filter(
      (c) =>
        c.type === "group" &&
        !c.archived &&
        (!c.status || c.status === "active") &&
        (!c.membership_epochs || c.membership_epochs.some((e) => !e.left_at)),
    )
    .map((c) => ({
      ...c.group,
      conversation_id: c.conversation_id,
      name: c.group?.name || t("communication.groupChats"),
      announcement: c.group?.announcement || null,
      owner_user_id: c.group?.owner_user_id || null,
      mute_all: !!c.group?.mute_all,
      is_member: true,
    }));
  const rows = (managed ? management : joined)
    .filter(
      (g) =>
        (kind === "all" || (g.group_kind || "department") === kind) &&
        g.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
    )
    .sort(
      (a, b) =>
        Number(b.group_kind === "company") - Number(a.group_kind === "company"),
    );
  const create = async (company = false) => {
    const payload = JSON.stringify({ company, name: name.trim(), members });
    if (request.current?.payload !== payload)
      request.current = { payload, id: newClientMessageId() };
    setBusy(true);
    setError("");
    try {
      const result = company
        ? await communicationApi.createCompanyGroup({
            request_id: request.current.id,
          })
        : await communicationApi.createProjectGroup({
            request_id: request.current.id,
            name: name.trim(),
            user_ids: members,
          });
      request.current = undefined;
      setCreating(false);
      setName("");
      setMembers([]);
      try {
        await client.synchronize({ throwOnError: true });
      } catch {
        setCreatedGroup(result.conversation_id);
        setError(t("communication.groupRefreshFailed"));
        return;
      }
      setCreatedGroup(undefined);
      navigate(`/messages/${encodeURIComponent(result.conversation_id)}`);
    } catch (e) {
      setError(communicationErrorText(e, t));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div style={{ padding: 12, overflow: "auto" }}>
      <Space direction="vertical" style={{ width: "100%" }}>
        {error && <Alert type="warning" showIcon message={error} />}
        {createdGroup && (
          <Button
            loading={busy}
            onClick={() => {
              setBusy(true);
              void client
                .synchronize({ throwOnError: true })
                .then(() => {
                  setError("");
                  navigate(`/messages/${encodeURIComponent(createdGroup)}`);
                  setCreatedGroup(undefined);
                })
                .catch((e) => setError(communicationErrorText(e, t)))
                .finally(() => setBusy(false));
            }}
          >
            {t("communication.openGroupChat")}
          </Button>
        )}
        <Space wrap>
          {canCreate && (
            <Button
              type="primary"
              onClick={() => {
                setError("");
                setCreating(true);
              }}
            >
              {t("communication.createProjectGroup")}
            </Button>
          )}
          {canManage && (
            <Button loading={busy} onClick={() => void create(true)}>
              {t("communication.enableCompanyGroup")}
            </Button>
          )}
        </Space>
        {canManage && (
          <Space>
            <Switch size="small" checked={managed} onChange={setManaged} />
            {t("communication.manageGroups")}
          </Space>
        )}
        <Input.Search
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("communication.searchGroupChats")}
        />
        <Select
          aria-label={t("communication.groupKindFilter")}
          value={kind}
          onChange={setKind}
          style={{ width: "100%" }}
          options={["all", "company", "department", "project"].map((value) => ({
            value,
            label: t(`communication.groupKinds.${value}`),
          }))}
        />
        <List
          dataSource={rows}
          locale={{
            emptyText: <Empty description={t("communication.noGroupChats")} />,
          }}
          renderItem={(g) => (
            <List.Item>
              <Button
                type="text"
                style={{
                  width: "100%",
                  height: "auto",
                  textAlign: "left",
                  whiteSpace: "normal",
                  justifyContent: "start",
                }}
                onClick={() =>
                  managed
                    ? setDetail(g)
                    : navigate(
                        `/messages/${encodeURIComponent(g.conversation_id!)}`,
                      )
                }
              >
                <Avatar icon={<Users size={18} />} />
                <span>
                  {g.name}
                  <br />
                  <Tag>
                    {t(
                      `communication.groupKinds.${
                        g.group_kind || "department"
                      }`,
                    )}
                  </Tag>
                  {managed && (
                    <Tag>
                      {t(
                        g.is_member
                          ? "communication.groupJoined"
                          : "communication.groupManageOnly",
                      )}
                    </Tag>
                  )}
                  {g.status === "archived" && (
                    <Tag>{t("communication.groupArchived")}</Tag>
                  )}
                </span>
              </Button>
            </List.Item>
          )}
        />
      </Space>
      <Modal
        title={t("communication.createProjectGroup")}
        open={creating}
        onCancel={() => setCreating(false)}
        onOk={() => void create()}
        confirmLoading={busy}
        okButtonProps={{ disabled: !name.trim() }}
        destroyOnHidden
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          {error && <Alert type="warning" message={error} />}
          <label>
            {t("communication.groupName")}
            <Input
              autoFocus
              maxLength={100}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <Select
            mode="multiple"
            style={{ width: "100%" }}
            value={members}
            onChange={setMembers}
            optionFilterProp="label"
            placeholder={t("communication.selectGroupMembers")}
            options={Object.values(state.contacts)
              .filter(
                (p) =>
                  p.user_id !== state.profile?.user_id &&
                  p.can_message !== false,
              )
              .map((p) => ({ value: p.user_id, label: p.nickname }))}
          />
          <small>{t("communication.newMembersHistoryHint")}</small>
        </Space>
      </Modal>
      {detail?.conversation_id && (
        <GroupDetails
          managed
          open={!!detail}
          close={() => setDetail(undefined)}
          conversation={{
            conversation_id: detail.conversation_id,
            group: detail,
          }}
        />
      )}
    </div>
  );
}
