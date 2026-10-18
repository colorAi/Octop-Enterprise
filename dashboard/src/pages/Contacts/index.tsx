import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Empty,
  Input,
  List,
  Segmented,
  Spin,
  Tag,
  Tree,
} from "antd";
import type { DataNode } from "antd/es/tree";
import {
  ArrowLeft,
  Building2,
  ChevronRight,
  Network,
  MessageCircle,
  RefreshCw,
  Users,
} from "lucide-react";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useIsMobile } from "../../hooks/useIsMobile";
import { useConversationPageVisible } from "../../hooks/useConversationPageVisible";
import { userCan } from "../../utils/permissions";
import { useCommunication } from "../Messaging/hooks/CommunicationContext";
import {
  communicationErrorText,
  communicationUnreadCount,
} from "../Messaging/communicationUi";
import { ChatPane, ConversationList } from "../Messaging";
import GroupsPanel from "../Messaging/GroupsPanel";
import { PersonAvatar } from "../Messaging/PersonIdentity";
import {
  departmentPath,
  departmentPeople,
  directoryTree,
  type DirectoryNode,
} from "./directoryTree";
import styles from "./index.module.less";
import { useDirectoryExpansion } from "./useDirectoryExpansion";

export default function ContactsPage() {
  const pageVisible = useConversationPageVisible();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { personId, conversationId } = useParams();
  const [query] = useSearchParams();
  const departmentId = query.get("department");
  const unassigned = departmentId === "unassigned";
  const user = useCurrentUser();
  const isMobile = useIsMobile();
  const { client, state } = useCommunication();
  const canBrowse = userCan(user, "contacts"),
    canMessage = userCan(user, "messaging");
  const [mode, setMode] = useState<string>(
    canBrowse && !pathname.startsWith("/messages") ? "directory" : "recent",
  );
  const [search, setSearch] = useState("");
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState("");
  const activePerson = useRef(personId);
  useEffect(() => {
    activePerson.current = pageVisible ? personId : undefined;
    setOpening(false);
    setError("");
    return () => {
      activePerson.current = undefined;
    };
  }, [personId, pageVisible]);
  const company = state.directoryName || t("communication.contacts");
  const people = useMemo(
    () => Object.values(state.contacts).filter((person) => !person.is_admin),
    [state.contacts],
  );
  const nodes = useMemo(
    () => directoryTree(state.departments, people, search),
    [state.departments, people, search],
  );
  const directoryExpansion = useDirectoryExpansion(nodes, user?.id, search);
  const person = personId ? state.contacts[personId] : undefined;
  const department = state.departments.find(
    (d) => d.department_id === departmentId,
  );
  const members = unassigned
    ? people.filter((p) => !p.department_id)
    : department
    ? departmentPeople(department.department_id, state.departments, people)
    : people;
  const conversation = conversationId
    ? state.conversations[conversationId]
    : undefined;
  const hasDetail = !!(personId || conversationId || departmentId);
  const unread = communicationUnreadCount(state);
  const isOnline = (id: string) =>
    state.presence == null ? undefined : !!state.presence[id];
  const treeData = (items: DirectoryNode[]): DataNode[] =>
    items.map((node) => ({
      key: node.key,
      isLeaf: !!node.person || !node.children?.length,
      title: (
        <div className={styles.treeTitle}>
          {node.person ? (
            <PersonAvatar
              person={node.person}
              size={24}
              online={isOnline(node.person.user_id)}
            />
          ) : !node.department ? (
            <Users size={16} aria-hidden />
          ) : node.department.parent_id ? (
            <Network size={16} aria-hidden />
          ) : (
            <Building2 size={16} aria-hidden />
          )}
          <span
            className={styles.treeName}
            title={node.person?.nickname || node.department?.name}
          >
            {node.person?.nickname ||
              node.department?.name ||
              t("communication.unassigned")}
          </span>
          {node.person?.job_title && (
            <span className={styles.positionTag} title={node.person.job_title}>
              {node.person.job_title}
            </span>
          )}
          {!node.person && (
            <small
              aria-label={t("communication.peopleCount", { count: node.count })}
            >
              {node.count}
            </small>
          )}
          {node.person?.user_id === state.profile?.user_id && (
            <small>{t("communication.me")}</small>
          )}
        </div>
      ),
      children: node.children?.length ? treeData(node.children) : undefined,
    }));
  const openPerson = (id: string) =>
    navigate(`/contacts/${encodeURIComponent(id)}`);
  const openChat = async () => {
    if (!person) return;
    const id = person.user_id;
    setOpening(true);
    setError("");
    try {
      const conv = await client.direct(id);
      if (activePerson.current === id)
        navigate(`/messages/${encodeURIComponent(conv)}`);
    } catch (e) {
      if (activePerson.current === id) setError(communicationErrorText(e, t));
    } finally {
      if (activePerson.current === id) setOpening(false);
    }
  };
  const breadcrumb = (id: string | null | undefined) =>
    departmentPath(id, state.departments)
      .map((d) => d.name)
      .join(" / ") || t("communication.unassigned");
  const personList = (
    <List
      className={styles.personList}
      dataSource={members}
      locale={{
        emptyText: <Empty description={t("communication.emptyDepartment")} />,
      }}
      renderItem={(p) => (
        <List.Item>
          <button
            className={styles.personRow}
            onClick={() => openPerson(p.user_id)}
          >
            <PersonAvatar person={p} online={isOnline(p.user_id)} />
            <span className={styles.personInfo}>
              <strong>
                {p.nickname}{" "}
                {p.job_title && (
                  <span className={styles.positionTag} title={p.job_title}>
                    {p.job_title}
                  </span>
                )}
              </strong>
              <small>{breadcrumb(p.department_id)}</small>
            </span>
            <ChevronRight size={16} />
          </button>
        </List.Item>
      )}
    />
  );
  return (
    <div className={styles.root}>
      {(state.error || error) && (
        <Alert
          type="warning"
          showIcon
          message={error || communicationErrorText(state.error, t)}
          action={
            <Button size="small" onClick={() => void client.synchronize()}>
              {t("communication.retry")}
            </Button>
          }
        />
      )}
      <div className={styles.workspace}>
        {(!isMobile || !hasDetail) && (
          <aside className={styles.rail}>
            <div className={styles.railHeader}>
              <div>
                <Building2 size={19} />
                <strong title={company}>{company}</strong>
              </div>
              <Button
                type="text"
                size="small"
                aria-label={t("communication.refresh")}
                icon={<RefreshCw size={15} />}
                onClick={() => void client.synchronize()}
              />
            </div>
            {canMessage && (
              <small className={styles.connection} role="status">
                {t(`communication.connection.${state.realtime}`)}
              </small>
            )}
            {canMessage && (
              <Segmented
                className={styles.mode}
                block
                value={mode}
                options={[
                  ...(canBrowse
                    ? [
                        {
                          value: "directory",
                          label: t("communication.organizationTree"),
                        },
                      ]
                    : []),
                  {
                    value: "recent",
                    label: (
                      <span>
                        {t("communication.recentMessages")}{" "}
                        <Badge count={unread} size="small" />
                      </span>
                    ),
                  },
                  {
                    value: "groups",
                    label: t("communication.groupChats"),
                  },
                ]}
                onChange={(value) => setMode(String(value))}
              />
            )}
            {state.phase === "loading" ? (
              <div className={styles.loading}>
                <Spin />
              </div>
            ) : mode === "directory" && canBrowse ? (
              <>
                <Input.Search
                  className={styles.search}
                  allowClear
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t("communication.searchDirectory")}
                />
                <div className={styles.tree}>
                  {nodes.length ? (
                    <Tree
                      key={search ? "search:" + search : "directory"}
                      blockNode
                      expandedKeys={directoryExpansion.expandedKeys}
                      autoExpandParent={false}
                      onExpand={(_, info) =>
                        directoryExpansion.onExpand(
                          String(info.node.key),
                          info.expanded,
                        )
                      }
                      showIcon={false}
                      selectedKeys={
                        personId
                          ? ["person:" + personId]
                          : unassigned
                          ? ["unassigned"]
                          : departmentId
                          ? ["department:" + departmentId]
                          : []
                      }
                      treeData={treeData(nodes)}
                      onSelect={(keys) => {
                        const key = String(keys[0] ?? "");
                        if (key.startsWith("person:")) openPerson(key.slice(7));
                        else if (key.startsWith("department:"))
                          navigate(
                            `/contacts?department=${encodeURIComponent(
                              key.slice(11),
                            )}`,
                          );
                        else if (key === "unassigned")
                          navigate("/contacts?department=unassigned");
                        else navigate("/contacts");
                      }}
                    />
                  ) : (
                    <Empty
                      description={t("communication.noDirectoryResults")}
                    />
                  )}
                </div>
              </>
            ) : (
              <div className={styles.recent}>
                {mode === "groups" ? (
                  <GroupsPanel />
                ) : (
                  <ConversationList conversationId={conversationId} />
                )}
              </div>
            )}
            {mode === "directory" && (
              <footer className={styles.railFooter}>
                {t("communication.directorySummary", {
                  departments: state.departments.length,
                  people: people.length,
                })}
              </footer>
            )}
          </aside>
        )}
        {(!isMobile || hasDetail) &&
          (conversation && state.phase === "ready" ? (
            <ChatPane
              key={`${state.profile?.user_id}:${conversation.conversation_id}`}
              conversation={conversation}
              positions={client.scrollPositions}
            />
          ) : (
            <section className={styles.detail}>
              {isMobile && hasDetail && (
                <Button
                  className={styles.back}
                  type="text"
                  icon={<ArrowLeft size={17} />}
                  onClick={() =>
                    navigate(
                      personId || departmentId ? "/contacts" : "/messages",
                    )
                  }
                >
                  {company}
                </Button>
              )}
              {state.phase === "loading" ? (
                <div className={styles.loading}>
                  <Spin />
                </div>
              ) : person ? (
                <div className={styles.profile}>
                  <div className={styles.profileHero}>
                    <PersonAvatar
                      person={person}
                      size={76}
                      online={isOnline(person.user_id)}
                    />
                    <h2>{person.nickname}</h2>
                    {person.job_title && (
                      <span
                        className={styles.positionTag}
                        title={person.job_title}
                      >
                        {person.job_title}
                      </span>
                    )}
                    <p>{breadcrumb(person.department_id)}</p>
                    <Tag color={isOnline(person.user_id) ? "green" : "default"}>
                      {t(
                        `communication.presence.${
                          isOnline(person.user_id) === undefined
                            ? "unknown"
                            : isOnline(person.user_id)
                            ? "online"
                            : "offline"
                        }`,
                      )}
                    </Tag>
                  </div>
                  <dl>
                    <div>
                      <dt>{t("communication.company")}</dt>
                      <dd>{company}</dd>
                    </div>
                    <div>
                      <dt>{t("communication.departmentLabel")}</dt>
                      <dd>{breadcrumb(person.department_id)}</dd>
                    </div>
                    <div>
                      <dt>{t("communication.name")}</dt>
                      <dd>{person.nickname}</dd>
                    </div>
                  </dl>
                  <div className={styles.profileActions}>
                    {person.user_id === state.profile?.user_id ||
                    person.user_id.endsWith(`:${user?.id}`) ? (
                      <Tag>{t("communication.thisIsYou")}</Tag>
                    ) : (
                      <>
                        <Button
                          type="primary"
                          size="large"
                          icon={<MessageCircle size={18} />}
                          disabled={
                            !canMessage ||
                            !person.can_message ||
                            state.phase !== "ready"
                          }
                          loading={opening}
                          onClick={() => void openChat()}
                        >
                          {t("communication.startChat")}
                        </Button>
                        {(!canMessage || !person.can_message) && (
                          <p>{t("communication.messagingNotEnabled")}</p>
                        )}
                      </>
                    )}
                  </div>
                </div>
              ) : personId || conversationId ? (
                <div className={styles.loading}>
                  <Empty
                    description={t("communication.conversationUnavailable")}
                  />
                </div>
              ) : (
                <div className={styles.overview}>
                  <header>
                    {department?.parent_id ? (
                      <Network size={34} />
                    ) : (
                      <Building2 size={34} />
                    )}
                    <div>
                      <h2>
                        {unassigned
                          ? t("communication.unassigned")
                          : department?.name || company}
                      </h2>
                      <p>
                        {department
                          ? breadcrumb(department.department_id)
                          : t("communication.directoryIntro")}
                      </p>
                    </div>
                  </header>
                  <div className={styles.summary}>
                    <span>
                      <Users size={18} />
                      <strong>{members.length}</strong>
                      {t("communication.people")}
                    </span>
                    {!department && !unassigned && (
                      <span>
                        <Network size={18} />
                        <strong>{state.departments.length}</strong>
                        {t("communication.departments")}
                      </span>
                    )}
                  </div>
                  {department?.conversation_id &&
                    state.conversations[department.conversation_id] &&
                    canMessage && (
                      <Button
                        icon={<MessageCircle size={16} />}
                        onClick={() =>
                          navigate(
                            `/messages/${encodeURIComponent(
                              department.conversation_id!,
                            )}`,
                          )
                        }
                      >
                        {t("communication.openDepartmentGroup", {
                          name: department.name,
                        })}
                      </Button>
                    )}
                  {canBrowse ? (
                    personList
                  ) : (
                    <Empty description={t("communication.chooseChat")} />
                  )}
                </div>
              )}
            </section>
          ))}
      </div>
    </div>
  );
}
