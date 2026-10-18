import type { CommunicationAttachment } from "../../api/types/communication";
import { MessageFileCard } from "../Chat/components/MessageFileCard";
import PrivateImage from "./PrivateImage";
import styles from "./index.module.less";

export default function MessageAttachment({
  attachment,
  onLoad,
}: {
  attachment: CommunicationAttachment;
  onLoad?: () => void;
}) {
  if (attachment.purpose !== "chat_file")
    return <PrivateImage asset={attachment.asset_id} onLoad={onLoad} />;
  return (
    <div className={styles.fileAttachment}>
      <MessageFileCard
        url={`/api/im/media/${encodeURIComponent(
          attachment.asset_id,
        )}?kind=original`}
        filename={attachment.filename}
      />
      {attachment.size_bytes != null && (
        <small>
          {attachment.size_bytes >= 1024 * 1024
            ? `${(attachment.size_bytes / (1024 * 1024)).toFixed(1)} MB`
            : `${Math.max(1, Math.ceil(attachment.size_bytes / 1024))} KB`}
        </small>
      )}
    </div>
  );
}
