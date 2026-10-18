import { useEffect, useId, useState } from "react";
import { Button, Checkbox, Image, Space, Typography } from "antd";
import { File, ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { LocalArtifact } from "../../api/desktopRuntime";
import { useAuthImageSrc } from "../../hooks/useAuthImageSrc";
import type { ChatAttachment } from "../Chat/hooks/useChat";
import { isImageAttachment } from "../Chat/utils/chatAttachments";
import { isNativePreviewData } from "../Chat/components/LocalImagePreview";
import {
  allForwardSelection,
  type ForwardContent,
  type ForwardSelection,
} from "./forwardContent";
import styles from "./ForwardContentPicker.module.less";

function Thumbnail({ src, name }: { src: string; name: string }) {
  return src ? (
    <Image
      src={src}
      alt={name}
      width={64}
      height={64}
      className={styles.image}
      referrerPolicy="no-referrer"
      preview={{ mask: false }}
    />
  ) : (
    <ImageIcon size={24} aria-hidden="true" />
  );
}

function AttachmentThumbnail({
  file,
  name,
}: {
  file: ChatAttachment;
  name: string;
}) {
  const { src } = useAuthImageSrc(file.url, file.filename);
  return <Thumbnail src={src} name={name} />;
}

function LocalThumbnail({ file, name }: { file: LocalArtifact; name: string }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let cancelled = false;
    setSrc("");
    void window.__octopRuntime
      ?.previewImage?.({ path: file.path, sha256: file.sha256 })
      .then((preview) => {
        if (!cancelled && isNativePreviewData(preview.data_url))
          setSrc(preview.data_url);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [file.path, file.sha256]);
  return <Thumbnail src={src} name={name} />;
}

function ContentRow({
  name,
  image,
  attachment,
  local,
  checked,
  disabled,
  onChange,
}: {
  name: string;
  image: boolean;
  attachment?: ChatAttachment;
  local?: LocalArtifact;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <div className={styles.item}>
      <Checkbox
        id={id}
        aria-label={name}
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <div className={styles.thumbnail}>
        {image ? (
          local ? (
            <LocalThumbnail file={local} name={name} />
          ) : (
            <AttachmentThumbnail file={attachment!} name={name} />
          )
        ) : (
          <File size={24} aria-hidden="true" />
        )}
      </div>
      <label className={styles.filename} htmlFor={id}>
        {name}
      </label>
    </div>
  );
}

export default function ForwardContentPicker({
  content,
  selection,
  onChange,
  disabled,
}: {
  content: ForwardContent;
  selection: ForwardSelection;
  onChange: (value: ForwardSelection) => void;
  disabled: boolean;
}) {
  const { t } = useTranslation();
  const files = [
    ...content.attachments.map((attachment, index) => ({
      key: attachment.url,
      image: isImageAttachment(attachment),
      name:
        attachment.filename ||
        `${t(
          isImageAttachment(attachment)
            ? "communication.image"
            : "communication.file",
        )} ${index + 1}`,
      attachment,
      local: undefined,
    })),
    ...content.localArtifacts.map((local) => ({
      key: local.path,
      image: isImageAttachment({ filename: local.path }),
      name: local.path.split(/[\\/]/).pop() || t("communication.file"),
      local,
      attachment: undefined,
    })),
  ];
  const only = (image: boolean) => {
    const picked = files.filter((file) => file.image === image);
    onChange({
      text: false,
      attachmentUrls: picked
        .filter((file) => file.attachment)
        .map((file) => file.key),
      localArtifactPaths: picked
        .filter((file) => file.local)
        .map((file) => file.key),
    });
  };
  return (
    <section aria-label={t("communication.forwardContent")}>
      <div className={styles.header}>
        <Typography.Text strong>
          {t("communication.forwardContent")}
        </Typography.Text>
        <Space size={4} wrap>
          <Button
            size="small"
            disabled={disabled}
            onClick={() => onChange(allForwardSelection(content))}
          >
            {t("communication.forwardAll")}
          </Button>
          {files.some((file) => file.image) && (
            <Button size="small" disabled={disabled} onClick={() => only(true)}>
              {t("communication.forwardImagesOnly")}
            </Button>
          )}
          {files.some((file) => !file.image) && (
            <Button
              size="small"
              disabled={disabled}
              onClick={() => only(false)}
            >
              {t("communication.forwardFilesOnly")}
            </Button>
          )}
        </Space>
      </div>
      <div className={styles.contents}>
        {content.text.trim() && (
          <div className={styles.textItem}>
            <Checkbox
              checked={selection.text}
              disabled={disabled}
              onChange={(event) =>
                onChange({ ...selection, text: event.target.checked })
              }
            >
              {t("communication.forwardText")}
            </Checkbox>
            <div className={styles.text}>{content.text}</div>
          </div>
        )}
        {files.map((file) => {
          const field = file.local ? "localArtifactPaths" : "attachmentUrls";
          return (
            <ContentRow
              key={`${field}:${file.key}`}
              name={file.name}
              image={file.image}
              attachment={file.attachment}
              local={file.local}
              checked={selection[field].includes(file.key)}
              disabled={disabled}
              onChange={(checked) =>
                onChange({
                  ...selection,
                  [field]: checked
                    ? [...selection[field], file.key]
                    : selection[field].filter((key) => key !== file.key),
                })
              }
            />
          );
        })}
      </div>
    </section>
  );
}
