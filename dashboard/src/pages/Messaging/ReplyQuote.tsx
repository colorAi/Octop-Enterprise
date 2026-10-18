import { useEffect, useState } from "react";
import { Alert, Modal, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { communicationApi } from "../../api/modules/communication";
import type { CommunicationMessage } from "../../api/types/communication";
import { useCommunication } from "./hooks/CommunicationContext";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerIsoDateTime } from "../../utils/formatMessageTime";
import MessageAttachment from "./MessageAttachment";
import styles from "./index.module.less";

export default function ReplyQuote({
  conversation,
  message,
}: {
  conversation: string;
  message: CommunicationMessage;
}) {
  const { t } = useTranslation();
  const { state } = useCommunication();
  const timezone = useServerTimezone();
  const [open, setOpen] = useState(false);
  const [original, setOriginal] = useState<CommunicationMessage>();
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const preview = message.reply_preview;
  const sender = (item: CommunicationMessage) =>
    state.contacts[item.sender_id]?.nickname ||
    (item.sender_id === state.profile?.user_id
      ? state.profile.nickname
      : t("communication.colleague"));
  const excerpt = (item: CommunicationMessage) =>
    item.recalled
      ? t("communication.recalled")
      : item.text ||
        item.attachments
          ?.map((a) => a.filename || t("communication.image"))
          .join("、") ||
        t("communication.image");
  useEffect(() => {
    if (!open || !message.reply_to_message_id) return;
    const controller = new AbortController();
    setLoading(true);
    setUnavailable(false);
    setOriginal(undefined);
    void communicationApi
      .message(conversation, message.reply_to_message_id, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setOriginal(value);
      })
      .catch(() => {
        if (!controller.signal.aborted) setUnavailable(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [open, conversation, message.reply_to_message_id, message.reply_preview]);
  return (
    <>
      <button
        type="button"
        className={styles.replyQuote}
        onClick={() => setOpen(true)}
        aria-label={t("communication.viewOriginal")}
      >
        {preview ? (
          <>
            <span>{sender(preview)}</span>
            <span>{excerpt(preview)}</span>
          </>
        ) : (
          <span>{t("communication.originalUnavailable")}</span>
        )}
      </button>
      <Modal
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        title={t("communication.originalMessage")}
        destroyOnHidden
      >
        {loading && <Spin />}
        {unavailable && (
          <Alert type="info" message={t("communication.originalUnavailable")} />
        )}
        {original && (
          <div className={styles.originalMessage}>
            <div className={styles.messageMeta}>
              <span>{sender(original)}</span>
              <time>
                {formatServerIsoDateTime(original.created_at, timezone)}
              </time>
            </div>
            {original.recalled ? (
              <em>{t("communication.recalled")}</em>
            ) : (
              <>
                <div className={styles.text}>{original.text}</div>
                {original.attachments?.map((asset) => (
                  <MessageAttachment key={asset.asset_id} attachment={asset} />
                ))}
              </>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
