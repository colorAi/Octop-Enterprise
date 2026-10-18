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
} from "antd";
import {
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
  const [text, setText] = useState("");
  const [assets, setAssets] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [older, setOlder] = useState(false);
  const [details, setDetails] = useState(false);
  const [reply, setReply] = useState<CommunicationMessage>();
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
    if (assets.length && text.trim().length > 2000) {
      setError(t("communication.captionLimit"));
      return;
    }
    const body = {
      client_message_id: newClientMessageId(),
      text: text.trim(),
      ...(assets.length ? { asset_ids: assets } : {}),
      ...(reply ? { reply_to_message_id: reply.message_id } : {}),
    };
    setText("");
    setAssets([]);
    setReply(undefined);
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
            const sender = own
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
                      : "communication.systemMessage",
                  )}
                </div>
              );
            return (
              <div
                key={message.message_id}
                className={`${chatStyles.messageBubble} ${
                  own ? chatStyles.userBubble : chatStyles.assistantBubble
                }`}
              >
                <div className={chatStyles.avatarCol}>
                  <MessageSender
                    name={
                      sender?.nickname ||
                      t(own ? "communication.you" : "communication.colleague")
                    }
                    avatar={<PersonAvatar person={sender} size={36} />}
                  />
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
          <Input.TextArea
            className={chatStyles.textarea}
            variant="borderless"
            value={text}
            disabled={!canSend}
            maxLength={assets.length ? 2000 : 8000}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder={t("communication.compose")}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                send();
              }
            }}
          />
          <div className={chatStyles.actionsRow}>
            <div className={chatStyles.secondaryActions}>
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
