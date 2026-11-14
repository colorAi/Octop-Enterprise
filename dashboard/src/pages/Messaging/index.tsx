import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Avatar,
  Badge,
  Button,
  Dropdown,
  Empty,
  Input,
  Modal,
  List,
  Spin,
  Tag,
} from "antd";
import {
  AtSign,
  ArrowLeft,
  ImagePlus,
  MoreHorizontal,
  Send,
  Paperclip,
  Reply,
  Undo2,
  Users,
  X,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { communicationApi } from "../../api/modules/communication";
import type {
  CommunicationMessage,
  Conversation,
} from "../../api/types/communication";
import { newClientMessageId } from "../../utils/communicationId";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { useConversationPageVisible } from "../../hooks/useConversationPageVisible";
import {
  formatServerHourMinute,
  formatServerIsoDateTime,
} from "../../utils/formatMessageTime";
import { useCommunication } from "./hooks/CommunicationContext";
import { conversationCanSend } from "./hooks/messagingState";
import {
  communicationErrorText,
  conversationName,
  visibleConversations,
} from "./communicationUi";
import MessageAttachment from "./MessageAttachment";
import ReplyQuote from "./ReplyQuote";
import GroupDetails from "./GroupDetails";
import { PersonAvatar, PersonJobTitle } from "./PersonIdentity";
import MessageSender from "../Chat/components/MessageSender";
import chatStyles from "../Chat/index.module.less";
import styles from "./index.module.less";
import ForwardMessageButton from "./ForwardMessageButton";
import ColleagueMentionInput, {
  type ColleagueMentionInputRef,
} from "./ColleagueMentionInput";
import { mentionedUserIds, type MentionDraft } from "./colleagueMentions";
import type { GroupExpertBinding } from "../../api/types/communication";
import { ExpertAvatar } from "./GroupExpertsPanel";
import ExpertCallStatus from "./ExpertCallStatus";

export function ChatPane({
  conversation,
  positions,
}: {
  conversation: Conversation;
  positions: Map<string, number>;
}) {
  const pageVisible = useConversationPageVisible();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { client, state } = useCommunication();
  const timezone = useServerTimezone();
  const conv = conversation.conversation_id;
  const peer = state.contacts[conversation.peer_user_id ?? ""];
  const [draft, setDraft] = useState<MentionDraft>({ text: "", mentions: [] });
  const text = draft.text;
  const composer = useRef<ColleagueMentionInputRef>(null);
  const [assets, setAssets] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [older, setOlder] = useState(false);
  const [details, setDetails] = useState(false);
  const [reply, setReply] = useState<CommunicationMessage>();
  const [expert, setExpert] = useState<GroupExpertBinding>();
  useEffect(() => {
    setExpert(undefined);
  }, [conv]);
  const scroller = useRef<HTMLDivElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const filePicker = useRef<HTMLInputElement>(null);
  const nearBottom = useRef(true);
  const restored = useRef(false);
  const alive = useRef(true);
  const messages = state.histories[conv];
  const pending = Object.entries(state.pending).filter(
    ([, m]) => m.conversation_id === conv,
  );
  const canSend =
    conversationCanSend(conversation, state.profile, state.contacts) &&
    state.phase === "ready";
  const report = (e: unknown) => {
    if (alive.current) setError(communicationErrorText(e, t));
  };
  const read = () => {
    if (
      pageVisible &&
      document.visibilityState === "visible" &&
      nearBottom.current &&
      messages?.length
    )
      void client.read(conv, messages[messages.length - 1].seq).catch(report);
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    if (!pageVisible) return;
    client.focus(conv);
    return () => client.focus(null);
  }, [client, conv, pageVisible]);
  useEffect(() => {
    if (!pageVisible) return;
    const element = scroller.current;
    if (!element || !messages) return;
    if (!restored.current) {
      const saved = positions.get(conv);
      element.scrollTop = saved ?? element.scrollHeight;
      nearBottom.current =
        saved === undefined ||
        element.scrollHeight - element.scrollTop - element.clientHeight < 64;
      restored.current = true;
    } else if (nearBottom.current) element.scrollTop = element.scrollHeight;
    if (
      document.visibilityState === "visible" &&
      nearBottom.current &&
      messages.length
    )
      void client.read(conv, messages[messages.length - 1].seq).catch((e) => {
        if (alive.current) setError(communicationErrorText(e, t));
      });
  }, [messages, pending.length, client, conv, positions, t, pageVisible]);
  useEffect(() => {
    if (!pageVisible) return;
    const onVisible = () => {
      if (
        document.visibilityState === "visible" &&
        nearBottom.current &&
        messages?.length
      )
        void client
          .read(conv, messages[messages.length - 1].seq)
          .catch(() => undefined);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [client, conv, messages, pageVisible]);
  const send = () => {
    if (!canSend || uploading || (!text.trim() && !assets.length)) return;
    if (expert && !text.trim()) {
      setError(t("groupExperts.questionRequired"));
      return;
    }
    if (assets.length && text.trim().length > 2000) {
      setError(t("communication.captionLimit"));
      return;
    }
    const mentionIds =
      conversation.type === "group" ? mentionedUserIds(draft) : [];
    if (mentionIds.length > 50) {
      setError(t("communication.mentionLimit"));
      return;
    }
    const body = {
      client_message_id: newClientMessageId(),
      text: text.trim(),
      ...(assets.length ? { asset_ids: assets } : {}),
      ...(reply ? { reply_to_message_id: reply.message_id } : {}),
      ...(mentionIds.length ? { mention_user_ids: mentionIds } : {}),
      ...(expert ? { mention_expert_id: expert.binding_id } : {}),
    };
    setDraft({ text: "", mentions: [] });
    setAssets([]);
    setReply(undefined);
    setExpert(undefined);
    nearBottom.current = true;
    setError("");
    void client.send(conv, body).catch(report);
  };
  const upload = async (files: FileList | null, image = true) => {
    if (!files?.length) return;
    if (assets.length + files.length > 9) {
      setError(t("communication.attachmentLimit"));
      return;
    }
    setUploading(true);
    setError("");
    try {
      for (const file of Array.from(files)) {
        const asset = await (image ? client.image(file) : client.file(file));
        if (alive.current) setAssets((old) => [...old, asset]);
      }
    } catch (e) {
      report(e);
    } finally {
      if (alive.current) setUploading(false);
      if (picker.current) picker.current.value = "";
      if (filePicker.current) filePicker.current.value = "";
    }
  };
  const mutate = (action: () => Promise<unknown>) => {
    setError("");
    void client.mutate(action).catch(report);
  };
  const mentionSender = async (userId: string) => {
    if (!canSend || conversation.type !== "group") return;
    setError("");
    try {
      const { items } = await communicationApi.members(conv);
      if (!alive.current) return;
      const member = items.find(
        (item) =>
          item.user_id === userId &&
          !item.archived &&
          item.status !== "disabled" &&
          item.status !== "deleted",
      );
      if (!member) {
        setError(t("communication.mentionMemberUnavailable"));
        return;
      }
      composer.current?.mentionMember(member);
    } catch (e) {
      report(e);
    }
  };
  const loadOlder = async () => {
    if (older) return;
    setOlder(true);
    const height = scroller.current?.scrollHeight ?? 0;
    try {
      await client.loadHistory(conv, true);
      requestAnimationFrame(() => {
        if (scroller.current)
          scroller.current.scrollTop += scroller.current.scrollHeight - height;
      });
    } catch (e) {
      report(e);
    } finally {
      if (alive.current) setOlder(false);
    }
  };
  const confirmClear = () =>
    Modal.confirm({
      title: t("communication.clearHistory"),
      content: t("communication.clearHint"),
      okText: t("communication.clearHistory"),
      cancelText: t("communication.cancel"),
      onOk: async () => {
        await client.mutate(() => communicationApi.clear(conv));
        positions.delete(conv);
      },
    });
  const confirmDelete = () =>
    Modal.confirm({
      title: t("communication.deleteConversation"),
      content: t("communication.deleteConversationHint"),
      okText: t("communication.deleteConversation"),
      cancelText: t("communication.cancel"),
      okButtonProps: { danger: true },
      onOk: async () => {
        await client.mutate(() => communicationApi.deleteConversation(conv));
        positions.delete(conv);
        navigate("/messages");
      },
    });
  return (
    <section className={`${chatStyles.chatMain} ${styles.pane}`}>
      <header className={`${chatStyles.chatTitleBar} ${styles.paneHeader}`}>
        <Button
          className={styles.mobileBack}
          type="text"
          aria-label={t("communication.back")}
          icon={<ArrowLeft size={18} />}
          onClick={() => navigate("/messages")}
        />
        {conversation.type === "group" ? (
          <Avatar icon={<Users size={18} />} />
        ) : (
          <PersonAvatar person={peer} />
        )}
        <div className={styles.heading}>
          <strong className={styles.identityLine}>
            <span>{conversationName(conversation, state, t)}</span>
            {conversation.type === "private" && (
              <PersonJobTitle person={peer} />
            )}
          </strong>
          <small>
            {t(
              conversation.type === "group"
                ? `communication.groupKinds.${
                    conversation.group?.group_kind || "department"
                  }`
                : "communication.directChat",
            )}
          </small>
        </div>
        {conversation.type === "group" && !conversation.archived && (
          <Button
            type="text"
            onClick={() => setDetails(true)}
            aria-label={t("communication.groupDetails")}
            icon={<Users size={18} />}
          />
        )}
        <Dropdown
          trigger={["click"]}
          menu={{
            items: [
              {
                key: "pin",
                label: t(
                  conversation.pinned
                    ? "communication.unpin"
                    : "communication.pin",
                ),
              },
              {
                key: "mute",
                label: t(
                  conversation.muted
                    ? "communication.unmute"
                    : "communication.mute",
                ),
              },
              { key: "hide", label: t("communication.hide") },
              {
                key: "delete",
                label: t("communication.deleteConversation"),
                danger: true,
              },
              {
                key: "clear",
                label: t("communication.clearHistory"),
                danger: true,
              },
            ],
            onClick: ({ key }) => {
              if (key === "pin")
                mutate(() =>
                  communicationApi.state(conv, {
                    pinned: !conversation.pinned,
                  }),
                );
              if (key === "mute")
                mutate(() =>
                  communicationApi.state(conv, { muted: !conversation.muted }),
                );
              if (key === "hide") {
                mutate(() => communicationApi.hide(conv));
                navigate("/messages");
              }
              if (key === "clear") confirmClear();
              if (key === "delete") confirmDelete();
            },
          }}
        >
          <Button
            type="text"
            aria-label={t("communication.actions")}
            icon={<MoreHorizontal size={19} />}
          />
        </Dropdown>
      </header>
      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          closable
          onClose={() => setError("")}
        />
      )}
      {conversation.archived && (
        <Alert type="info" message={t("communication.archivedHint")} />
      )}
      <div
        className={chatStyles.messageList}
        ref={scroller}
        onScroll={() => {
          const element = scroller.current;
          if (element) {
            nearBottom.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              64;
            positions.set(conv, element.scrollTop);
            if (nearBottom.current) read();
          }
        }}
      >
        {state.historyPages[conv]?.more && (
          <div className={styles.loadEarlier}>
            <Button loading={older} onClick={() => void loadOlder()}>
              {t("communication.loadEarlier")}
            </Button>
          </div>
        )}
        {!messages ? (
          <Spin />
        ) : (
          messages.length === 0 && (
            <Empty description={t("communication.noMessages")} />
          )
        )}
        <div
          className={`${chatStyles.messageListInner} ${styles.historyInner}`}
        >
          {messages?.map((message) => {
            const own = message.sender_id === state.profile?.user_id;
            const sender = message.expert
              ? {
                  user_id: message.sender_id,
                  nickname: message.expert.display_name,
                }
              : own
              ? state.contacts[message.sender_id] ?? state.profile
              : state.contacts[message.sender_id];
            if (message.type === "system")
              return (
                <div key={message.message_id} className={styles.system}>
                  {t(
                    [
                      "department.members.changed",
                      "group.members.changed",
                    ].includes(message.system_kind || "")
                      ? "communication.membersChanged"
                      : message.system_kind?.startsWith("group.expert.")
                      ? `groupExperts.system.${message.system_kind
                          .split(".")
                          .pop()}`
                      : "communication.systemMessage",
                  )}
                </div>
              );
            const avatar = (
              <MessageSender
                name={
                  sender?.nickname ||
                  t(own ? "communication.you" : "communication.colleague")
                }
                avatar={
                  message.expert ? (
                    <ExpertAvatar url={message.expert.avatar.url} size={36} />
                  ) : (
                    <PersonAvatar person={sender} size={36} />
                  )
                }
              />
            );
            return (
              <div
                key={message.message_id}
                className={`${chatStyles.messageBubble} ${styles.messageRow} ${
                  own ? chatStyles.userBubble : chatStyles.assistantBubble
                }`}
              >
                <div className={chatStyles.avatarCol}>
                  {conversation.type === "group" &&
                  !own &&
                  canSend &&
                  (!message.expert || message.expert.state === "active") ? (
                    <Dropdown
                      trigger={["contextMenu"]}
                      menu={{
                        items: [
                          {
                            key: "mention",
                            label: t("communication.mentionMember", {
                              name:
                                sender?.nickname ||
                                t("communication.colleague"),
                            }),
                          },
                        ],
                        onClick: () => {
                          if (!message.expert) {
                            void mentionSender(message.sender_id);
                            return;
                          }
                          void communicationApi
                            .groupExperts(conv)
                            .then((value) => {
                              const binding = value.items.find(
                                (v) =>
                                  v.binding_id === message.expert?.binding_id &&
                                  v.can_call &&
                                  v.state === "active",
                              );
                              if (
                                binding &&
                                value.ready &&
                                value.experts_enabled
                              )
                                composer.current?.mentionExpert(binding);
                              else
                                setError(t("groupExperts.expertUnavailable"));
                            })
                            .catch(report);
                        },
                      }}
                    >
                      <span>{avatar}</span>
                    </Dropdown>
                  ) : (
                    avatar
                  )}
                </div>
                <div
                  className={`${chatStyles.bubbleContent} ${styles.message} ${
                    own ? styles.own : ""
                  }`}
                >
                  <div className={styles.messageMeta}>
                    <span>
                      {own
                        ? t("communication.you")
                        : sender?.nickname || t("communication.colleague")}
                    </span>
                    <PersonJobTitle person={sender} />
                    {message.expert && (
                      <span>
                        {t(
                          message.expert.state === "active"
                            ? "groupExperts.expert"
                            : "groupExperts.removed",
                        )}
                      </span>
                    )}
                    {!message.recalled &&
                      !own &&
                      state.profile &&
                      (message.mention_all ||
                        message.mention_user_ids?.includes(
                          state.profile.user_id,
                        )) && (
                        <span className={styles.mentionBadge}>
                          {t("communication.mentionedYou")}
                        </span>
                      )}
                    <time
                      title={formatServerIsoDateTime(
                        message.created_at,
                        timezone,
                      )}
                    >
                      {formatServerHourMinute(message.created_at, timezone)}
                    </time>
                  </div>
                  <div
                    className={`${styles.messageContent} ${
                      own ? chatStyles.userText : ""
                    }`}
                  >
                    {message.recalled ? (
                      <em>{t("communication.recalled")}</em>
                    ) : (
                      <>
                        {message.reply_to_message_id && (
                          <ReplyQuote conversation={conv} message={message} />
                        )}
                        {message.mention_expert_id && (
                          <Tag>
                            @
                            {message.expert_call?.expert_name ||
                              t("groupExperts.expert")}
                          </Tag>
                        )}
                        {message.text && (
                          <div className={styles.text}>{message.text}</div>
                        )}
                        {message.attachments?.map((asset) => (
                          <MessageAttachment
                            key={asset.asset_id}
                            attachment={asset}
                            onLoad={() => {
                              if (nearBottom.current && scroller.current)
                                scroller.current.scrollTop =
                                  scroller.current.scrollHeight;
                            }}
                          />
                        ))}
                      </>
                    )}
                  </div>
                  {!message.recalled && (
                    <ExpertCallStatus call={message.expert_call} />
                  )}
                  {!message.recalled && (
                    <div className={styles.messageActions}>
                      <button
                        type="button"
                        disabled={!canSend}
                        onClick={() => setReply(message)}
                      >
                        <Reply size={12} aria-hidden="true" />{" "}
                        {t("communication.reply")}
                      </button>
                      <ForwardMessageButton
                        source={{
                          kind: "company",
                          conversationId: conv,
                          message,
                        }}
                        className=""
                      />
                      {own && (
                        <button
                          type="button"
                          onClick={() =>
                            mutate(() =>
                              communicationApi.recall(message.message_id),
                            )
                          }
                        >
                          <Undo2 size={12} aria-hidden="true" />{" "}
                          {t("communication.recall")}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          {pending.map(([id, item]) => (
            <div key={id} className={`${styles.message} ${styles.own}`}>
              <div className={styles.messageContent}>
                <div className={styles.text}>{item.body.text}</div>
                {item.body.asset_ids?.map((asset) => (
                  <MessageAttachment
                    key={asset}
                    attachment={
                      state.assets[asset] ?? { asset_id: asset, state: "ready" }
                    }
                    onLoad={() => {
                      if (nearBottom.current && scroller.current)
                        scroller.current.scrollTop =
                          scroller.current.scrollHeight;
                    }}
                  />
                ))}
              </div>
              <small>
                {t(
                  item.status === "sending"
                    ? "communication.sending"
                    : "communication.sendFailed",
                )}
              </small>
              {item.status === "failed" && (
                <>
                  <small>{communicationErrorText(item.error, t)}</small>
                  <Button
                    size="small"
                    disabled={!canSend}
                    onClick={() =>
                      void client.send(conv, item.body).catch(report)
                    }
                  >
                    {t("communication.retry")}
                  </Button>
                </>
              )}
            </div>
          ))}
        </div>
      </div>
      <div className={chatStyles.chatInput}>
        <div className={chatStyles.inputWrapper}>
          {!canSend && (
            <div className={styles.sendBlocked}>
              {t(
                conversation.archived
                  ? "communication.archivedHint"
                  : "communication.sendBlocked",
              )}
            </div>
          )}
          {reply && (
            <div className={styles.replyDraft}>
              <span>
                {t("communication.reply")}:{" "}
                {reply.text ||
                  t(
                    reply.type === "file"
                      ? "communication.file"
                      : "communication.image",
                  )}
              </span>
              <Button
                type="text"
                size="small"
                aria-label={t("communication.cancel")}
                icon={<X size={14} />}
                onClick={() => setReply(undefined)}
              />
            </div>
          )}
          {assets.length > 0 && (
            <div className={styles.attachments}>
              {assets.map((asset) => (
                <div key={asset}>
                  <MessageAttachment
                    attachment={
                      state.assets[asset] ?? { asset_id: asset, state: "ready" }
                    }
                  />
                  <Button
                    size="small"
                    onClick={() =>
                      setAssets((old) => old.filter((id) => id !== asset))
                    }
                  >
                    {t("communication.remove")}
                  </Button>
                </div>
              ))}
            </div>
          )}
          {expert && (
            <div className={styles.replyDraft}>
              <ExpertAvatar url={expert.avatar.url} size={24} />{" "}
              <span>@{expert.display_name}</span>
              <Button size="small" onClick={() => setExpert(undefined)}>
                {t("communication.remove")}
              </Button>
            </div>
          )}
          <ColleagueMentionInput
            ref={composer}
            draft={draft}
            onChange={setDraft}
            onExpert={setExpert}
            onSend={send}
            groupId={
              conversation.type === "group" && pageVisible ? conv : undefined
            }
            userId={state.profile?.user_id}
            disabled={!canSend}
            maxLength={assets.length ? 2000 : 8000}
          />
          <div className={chatStyles.actionsRow}>
            <div className={chatStyles.secondaryActions}>
              {conversation.type === "group" && (
                <button
                  type="button"
                  className={chatStyles.secondaryBtn}
                  disabled={!canSend}
                  title={t("communication.mentionColleague")}
                  aria-label={t("communication.mentionColleague")}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => composer.current?.mention()}
                >
                  <AtSign size={18} />
                </button>
              )}
              <input
                ref={picker}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                multiple
                hidden
                onChange={(e) => void upload(e.target.files)}
              />
              <input
                ref={filePicker}
                type="file"
                aria-label={t("communication.file")}
                multiple
                hidden
                onChange={(e) => void upload(e.target.files, false)}
              />
              <button
                type="button"
                className={chatStyles.secondaryBtn}
                disabled={!canSend || uploading || assets.length >= 9}
                title={t("communication.image")}
                aria-label={t("communication.image")}
                onClick={() => picker.current?.click()}
              >
                <ImagePlus size={18} />
              </button>
              <button
                type="button"
                className={chatStyles.secondaryBtn}
                disabled={!canSend || uploading || assets.length >= 9}
                title={t("communication.file")}
                aria-label={t("communication.file")}
                onClick={() => filePicker.current?.click()}
              >
                <Paperclip size={18} />
              </button>
              {uploading && <Spin size="small" />}
              <span className={styles.enterHint}>
                {t("communication.enterHint")}
              </span>
            </div>
            <div className={chatStyles.inputActions}>
              <button
                type="button"
                className={chatStyles.sendBtn}
                disabled={
                  !canSend || uploading || (!text.trim() && !assets.length)
                }
                title={t("communication.send")}
                aria-label={t("communication.send")}
                onClick={send}
              >
                <Send size={17} />
              </button>
            </div>
          </div>
        </div>
      </div>
      {conversation.type === "group" && (
        <GroupDetails
          conversation={conversation}
          open={details}
          close={() => setDetails(false)}
        />
      )}
    </section>
  );
}

export function ConversationList({
  conversationId,
  groupsOnly = false,
}: {
  conversationId?: string;
  groupsOnly?: boolean;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { state } = useCommunication();
  const [search, setSearch] = useState("");
  const conversations = visibleConversations(state, {
    includeHidden: groupsOnly,
  }).filter(
    (row) =>
      (!groupsOnly ||
        (row.type === "group" &&
          !row.archived &&
          row.status !== "dissolved")) &&
      [
        conversationName(row, state, t),
        row.type === "private"
          ? state.contacts[row.peer_user_id ?? ""]?.job_title
          : "",
      ]
        .join(" ")
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <>
      <Input.Search
        className={styles.search}
        placeholder={t(
          groupsOnly
            ? "communication.searchGroupChats"
            : "communication.searchConversations",
        )}
        allowClear
        value={search}
        onChange={(e) => setSearch(e.target.value)}
      />
      <List
        dataSource={conversations}
        locale={{
          emptyText: (
            <Empty
              description={t(
                groupsOnly
                  ? "communication.noGroupChats"
                  : "communication.noConversations",
              )}
            />
          ),
        }}
        renderItem={(row) => (
          <List.Item
            className={`${styles.conversation} ${
              conversationId === row.conversation_id ? styles.selected : ""
            }`}
          >
            <button
              onClick={() =>
                navigate(`/messages/${encodeURIComponent(row.conversation_id)}`)
              }
            >
              <Badge count={row.unread ?? 0} size="small">
                {row.type === "group" ? (
                  <Avatar icon={<Users size={18} />} />
                ) : (
                  <PersonAvatar
                    person={state.contacts[row.peer_user_id ?? ""]}
                  />
                )}
              </Badge>
              <div>
                <strong className={styles.identityLine}>
                  <span>{conversationName(row, state, t)}</span>
                  {row.type === "private" && (
                    <PersonJobTitle
                      person={state.contacts[row.peer_user_id ?? ""]}
                    />
                  )}
                </strong>
                <small>
                  {row.last_message?.type === "image"
                    ? t("communication.image")
                    : row.last_message?.type === "system"
                    ? t("communication.systemMessage")
                    : row.last_message?.preview === "[已撤回]"
                    ? t("communication.recalled")
                    : row.last_message?.preview ||
                      t("communication.noMessages")}
                </small>
              </div>
              <span>
                {row.pinned ? "↑" : ""}
                {row.muted ? "·" : ""}
              </span>
            </button>
          </List.Item>
        )}
      />
    </>
  );
}
