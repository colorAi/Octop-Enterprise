import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Collapse,
  Dropdown,
  Modal,
  Button,
  Drawer,
  Input,
  List,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
} from "antd";
import { MoreHorizontal, UserPlus } from "lucide-react";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { userCan } from "../../utils/permissions";
import styles from "./GroupDetails.module.less";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { communicationApi } from "../../api/modules/communication";
import type {
  CommunicationUser,
  Conversation,
  GroupInfo,
  GroupMember,
} from "../../api/types/communication";
import { newClientMessageId } from "../../utils/communicationId";
import { useCommunication } from "./hooks/CommunicationContext";
import { communicationErrorText } from "./communicationUi";
import { PersonAvatar, PersonJobTitle } from "./PersonIdentity";

export default function GroupDetails({
  conversation,
  open,
  close,
  managed = false,
}: {
  conversation: Pick<Conversation, "conversation_id" | "group" | "archived">;
  open: boolean;
  close: () => void;
  managed?: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const user = useCurrentUser();
  const organizationManagement = managed || userCan(user, "departments");
  const [modal, modalContext] = Modal.useModal();
  const [inviting, setInviting] = useState(false);
  const { client, state } = useCommunication();
  const [group, setGroup] = useState<GroupInfo>();
  const [members, setMembers] = useState<GroupMember[]>([]);
  const [people, setPeople] = useState<CommunicationUser[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [name, setName] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const [nickname, setNickname] = useState("");
  const [invite, setInvite] = useState<string[]>([]);
  const [muteAll, setMuteAll] = useState(false);
  const [canInvite, setCanInvite] = useState(false);
  const requests = useRef(
    new Map<string, { id: string; action: (id: string) => Promise<unknown> }>(),
  );
  const id = conversation.conversation_id;
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setError("");
    setGroup(undefined);
    setMembers([]);
    setInvite([]);
    requests.current.clear();
    const load = organizationManagement
      ? communicationApi.organization
          .group(id, controller.signal)
          .then((info) => ({ info, roster: info.members }))
      : Promise.all([
          communicationApi.group(id, controller.signal),
          communicationApi.members(id, controller.signal),
        ]).then(([info, roster]) => ({ info, roster: roster.items }));
    void load
      .then(({ info, roster }) => {
        if (controller.signal.aborted) return;
        setGroup(info);
        setMembers(roster);
        setName(info.name);
        setAnnouncement(info.announcement || "");
        setMuteAll(info.mute_all);
        setCanInvite(!!info.members_can_invite);
        setNickname(
          roster.find((m) => m.user_id === state.profile?.user_id)
            ?.my_nickname || "",
        );
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(communicationErrorText(e, t));
      });
    if (organizationManagement) {
      void communicationApi.organization
        .users(controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) setPeople(value.items);
        })
        .catch((e) => {
          if (!controller.signal.aborted)
            setError(communicationErrorText(e, t));
        });
    }
    return () => controller.abort();
  }, [open, id, organizationManagement, revision, state.profile?.user_id, t]);
  const active = group?.status !== "archived" && group?.status !== "dissolved";
  const isOwner = organizationManagement || group?.my_role === "owner";
  const canManage =
    organizationManagement || isOwner || group?.my_role === "admin";
  const isMember =
    group?.is_member ??
    members.some((m) => m.user_id === state.profile?.user_id);
  const project = group?.group_kind === "project";
  const run = async (
    key: string,
    action: (requestId: string) => Promise<unknown>,
    after?: () => void,
  ) => {
    const request = requests.current.get(key) || {
      id: newClientMessageId(),
      action,
    };
    requests.current.set(key, request);
    setBusy(true);
    setError("");
    try {
      await client.mutate(() => request.action(request.id));
      requests.current.delete(key);
      setRevision((v) => v + 1);
      after?.();
    } catch (e) {
      setError(communicationErrorText(e, t));
    } finally {
      setBusy(false);
    }
  };
  const candidates = (
    organizationManagement ? people : Object.values(state.contacts)
  ).filter(
    (p) =>
      p.can_message !== false &&
      (!p.status || p.status === "active") &&
      !members.some((m) => m.user_id === p.user_id),
  );
  const canInviteMembers =
    active &&
    (canManage || (project && group?.members_can_invite)) &&
    group?.group_kind !== "company";
  const confirm = (
    title: string,
    action: () => Promise<void>,
    danger = false,
  ) => {
    modal.confirm({
      title,
      onOk: action,
      okText: t("common.confirm"),
      cancelText: t("common.cancel"),
      okButtonProps: { danger },
    });
  };
  const memberActions = (member: GroupMember) => {
    if (!active || !canManage || member.role === "owner") return [];
    const muted =
      !!member.mute_until && Date.parse(member.mute_until) > Date.now();
    return [
      ...(isOwner
        ? [
            {
              key: "role",
              label: t(
                member.role === "admin"
                  ? "communication.removeGroupAdmin"
                  : "communication.makeGroupAdmin",
              ),
              onClick: () =>
                void run("role:" + member.user_id + member.role, (request_id) =>
                  communicationApi.groupMember(
                    id,
                    member.user_id,
                    {
                      request_id,
                      role: member.role === "admin" ? "member" : "admin",
                    },
                    organizationManagement,
                  ),
                ),
            },
          ]
        : []),
      ...(isOwner || member.role === "member"
        ? [
            {
              key: "mute",
              label: t(
                muted
                  ? "communication.unmuteMember"
                  : "communication.muteMemberHour",
              ),
              onClick: () => {
                const mute_until = muted
                  ? null
                  : new Date(Date.now() + 3600_000).toISOString();
                void run(
                  "mute:" + member.user_id + member.mute_until,
                  (request_id) =>
                    communicationApi.groupMember(
                      id,
                      member.user_id,
                      { request_id, mute_until },
                      organizationManagement,
                    ),
                );
              },
            },
          ]
        : []),
      ...(isOwner
        ? [
            {
              key: "owner",
              label: t("communication.transferOwner"),
              onClick: () =>
                confirm(
                  t("communication.confirmTransferOwner", {
                    name: member.nickname,
                  }),
                  () =>
                    run("owner:" + member.user_id, (request_id) =>
                      communicationApi.groupOwner(
                        id,
                        { request_id, user_id: member.user_id },
                        organizationManagement,
                      ),
                    ),
                ),
            },
          ]
        : []),
      ...(member.membership_source === "invited" &&
      (isOwner || member.role === "member")
        ? [
            {
              key: "remove",
              danger: true,
              label: t("communication.removeMember"),
              onClick: () =>
                confirm(
                  t("communication.confirmRemoveMember", {
                    name: member.nickname,
                  }),
                  () =>
                    run("remove:" + member.user_id, (request_id) =>
                      communicationApi.groupMembers(
                        id,
                        { request_id, user_ids: [member.user_id] },
                        true,
                        organizationManagement,
                      ),
                    ),
                  true,
                ),
            },
          ]
        : []),
    ];
  };
  return (
    <Drawer
      open={open}
      onClose={close}
      title={
        group?.name || conversation.group?.name || t("communication.groupChats")
      }
      width={440}
    >
      {modalContext}
      {error && (
        <Alert
          type="warning"
          showIcon
          message={error}
          style={{ marginBottom: 16 }}
        />
      )}
      {!group ? (
        !error && <Spin />
      ) : (
        <>
          <div className={styles.summary}>
            <span>
              {t(
                `communication.groupKinds.${group.group_kind || "department"}`,
              )}{" "}
              · {t("communication.peopleCount", { count: members.length })}
            </span>
            {canManage && (
              <Tag>
                {t(
                  isMember
                    ? "communication.groupJoined"
                    : "communication.groupManageOnly",
                )}
              </Tag>
            )}
            {!active && <Tag>{t("communication.groupArchived")}</Tag>}
          </div>
          {!isMember && canManage && (
            <Alert
              type="info"
              style={{ marginBottom: 16 }}
              message={t("communication.groupNotMemberHint")}
              action={
                active &&
                group.group_kind !== "company" &&
                state.profile?.user_id ? (
                  <Button
                    size="small"
                    loading={busy}
                    onClick={() =>
                      void run("join-self", (request_id) =>
                        communicationApi.groupMembers(
                          id,
                          { request_id, user_ids: [state.profile!.user_id] },
                          false,
                          true,
                        ),
                      )
                    }
                  >
                    {t("communication.joinGroup")}
                  </Button>
                ) : undefined
              }
            />
          )}
          {isMember && managed && (
            <Button
              style={{ marginBottom: 16 }}
              onClick={() => {
                close();
                navigate(`/messages/${encodeURIComponent(id)}`);
              }}
            >
              {t("communication.openGroupChat")}
            </Button>
          )}
          <section className={styles.announcement}>
            <h3>{t("communication.announcement")}</h3>
            <p>{group.announcement || t("communication.noAnnouncement")}</p>
          </section>
          <section aria-label={t("communication.members")}>
            <div className={styles.rosterHeader}>
              <h3>
                {t("communication.members")}{" "}
                <span
                  style={{ fontWeight: 400, color: "var(--fn-text-tertiary)" }}
                >
                  ({members.length})
                </span>
              </h3>
              {canInviteMembers && (
                <Button
                  size="small"
                  type="text"
                  icon={<UserPlus size={15} />}
                  onClick={() => setInviting((v) => !v)}
                >
                  {t("communication.inviteMembers")}
                </Button>
              )}
            </div>
            {canInviteMembers && inviting && (
              <div className={styles.invitation}>
                <Select
                  mode="multiple"
                  value={invite}
                  onChange={setInvite}
                  optionFilterProp="label"
                  placeholder={t("communication.selectGroupMembers")}
                  options={candidates.map((p) => ({
                    value: p.user_id,
                    label: p.nickname,
                  }))}
                />
                <Button
                  type="primary"
                  disabled={busy || !invite.length}
                  onClick={() =>
                    void run(
                      "invite:" + invite.join(","),
                      (request_id) =>
                        communicationApi.groupMembers(
                          id,
                          { request_id, user_ids: invite },
                          false,
                          organizationManagement,
                        ),
                      () => setInviting(false),
                    )
                  }
                >
                  {t("communication.inviteMembers")}
                </Button>
              </div>
            )}
            <List
              dataSource={members}
              renderItem={(member) => {
                const person = state.contacts[member.user_id] ?? member;
                const actions = memberActions(member);
                return (
                  <List.Item>
                    <div className={styles.member}>
                      <PersonAvatar person={person} size={36} />
                      <div className={styles.memberIdentity}>
                        <div className={styles.memberName}>
                          <span>{member.my_nickname || person.nickname}</span>
                          <PersonJobTitle person={person} />
                          {member.user_id === state.profile?.user_id && (
                            <span className={styles.hint}>
                              {t("communication.me")}
                            </span>
                          )}
                          {canManage &&
                            member.role &&
                            member.role !== "member" && (
                              <Tag>
                                {t(`communication.roles.${member.role}`)}
                              </Tag>
                            )}
                        </div>
                        {canManage && member.membership_source && (
                          <div className={styles.memberSource}>
                            {t(
                              `communication.memberSources.${member.membership_source}`,
                            )}
                          </div>
                        )}
                      </div>
                      {actions.length > 0 && (
                        <Dropdown trigger={["click"]} menu={{ items: actions }}>
                          <Button
                            size="small"
                            type="text"
                            disabled={busy}
                            icon={<MoreHorizontal size={18} />}
                            aria-label={t("communication.manageMember", {
                              name: member.nickname,
                            })}
                          />
                        </Dropdown>
                      )}
                    </div>
                  </List.Item>
                );
              }}
            />
          </section>
          <Collapse
            ghost
            className={styles.settings}
            items={[
              ...(isMember && active
                ? [
                    {
                      key: "nickname",
                      label: t("communication.myNickname"),
                      children: (
                        <Space.Compact style={{ width: "100%" }}>
                          <Input
                            value={nickname}
                            maxLength={16}
                            onChange={(e) => setNickname(e.target.value)}
                            placeholder={t("communication.myNickname")}
                          />
                          <Button
                            disabled={busy}
                            onClick={() =>
                              void run("nickname:" + nickname, () =>
                                communicationApi.nickname(id, nickname),
                              )
                            }
                          >
                            {t("communication.save")}
                          </Button>
                        </Space.Compact>
                      ),
                    },
                  ]
                : []),
              ...(canManage && active
                ? [
                    {
                      key: "management",
                      label: t("communication.groupManagementSettings"),
                      children: (
                        <div className={styles.form}>
                          {!project && (
                            <p className={styles.hint}>
                              {t("communication.compulsoryGroup")}
                            </p>
                          )}
                          <label>
                            {t("communication.groupName")}
                            <Input
                              value={name}
                              maxLength={100}
                              onChange={(e) => setName(e.target.value)}
                            />
                          </label>
                          <label>
                            {t("communication.announcement")}
                            <Input.TextArea
                              value={announcement}
                              maxLength={500}
                              rows={3}
                              onChange={(e) => setAnnouncement(e.target.value)}
                            />
                          </label>
                          <div className={styles.switchRow}>
                            <span>{t("communication.muteAll")}</span>
                            <Switch checked={muteAll} onChange={setMuteAll} />
                          </div>
                          {project && (
                            <div className={styles.switchRow}>
                              <span>{t("communication.membersCanInvite")}</span>
                              <Switch
                                checked={canInvite}
                                onChange={setCanInvite}
                              />
                            </div>
                          )}
                          <Button
                            type="primary"
                            disabled={busy || !name.trim()}
                            onClick={() =>
                              void run(
                                JSON.stringify([
                                  "settings",
                                  name,
                                  announcement,
                                  muteAll,
                                  canInvite,
                                ]),
                                (request_id) =>
                                  communicationApi.updateGroup(
                                    id,
                                    {
                                      request_id,
                                      name: name.trim(),
                                      announcement,
                                      mute_all: muteAll,
                                      ...(project
                                        ? { members_can_invite: canInvite }
                                        : {}),
                                    },
                                    organizationManagement,
                                  ),
                              )
                            }
                          >
                            {t("communication.save")}
                          </Button>
                        </div>
                      ),
                    },
                  ]
                : []),
            ]}
          />
          {project && active && (
            <div className={styles.footer}>
              {isMember && (
                <Popconfirm
                  title={t("communication.confirmLeaveGroup")}
                  disabled={group.my_role === "owner"}
                  onConfirm={() =>
                    run(
                      "leave",
                      (request_id) =>
                        communicationApi.leaveGroup(id, request_id),
                      close,
                    )
                  }
                >
                  <Button
                    danger
                    type="text"
                    disabled={busy || group.my_role === "owner"}
                  >
                    {t("communication.leaveGroup")}
                  </Button>
                </Popconfirm>
              )}
              {isOwner && (
                <Popconfirm
                  title={t("communication.confirmArchiveGroup")}
                  onConfirm={() =>
                    run(
                      "archive",
                      (request_id) =>
                        communicationApi.archiveGroup(
                          id,
                          request_id,
                          organizationManagement,
                        ),
                      close,
                    )
                  }
                >
                  <Button danger type="text" disabled={busy}>
                    {t("communication.archiveGroup")}
                  </Button>
                </Popconfirm>
              )}
              {group.my_role === "owner" && (
                <p className={styles.hint}>
                  {t("communication.ownerLeaveHint")}
                </p>
              )}
            </div>
          )}
        </>
      )}
    </Drawer>
  );
}
