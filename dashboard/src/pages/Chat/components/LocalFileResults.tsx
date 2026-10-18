import { LocalImagePreview, isLocalPreviewImage } from "./LocalImagePreview";
import { Alert, Button, Image, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { message } from "@/utils/antdMessage";
import { useDesktopWorkspace } from "@/context/DesktopWorkspaceContext";
import { requireDesktopRuntime } from "@/api/desktopRuntime";
import type { ChatMessage } from "../hooks/useChat";
import styles from "../index.module.less";

export function localFileResults(messages: ChatMessage[]) {
  const receipts = new Map<
    string,
    { id: string; path: string; device: string; completed: boolean }
  >();
  for (const message of messages) {
    if (
      !["local_write_file", "local_operation_status"].includes(
        message.toolData?.name ?? "",
      )
    )
      continue;
    try {
      const envelope = JSON.parse(message.toolData?.output ?? "") as Record<
        string,
        unknown
      >;
      if (envelope.execution_location !== "local") continue;
      const result = envelope.result as Record<string, unknown> | undefined;
      const id = result?.operation_id || envelope.call_id;
      if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id)) continue;
      // A transport completion alone is not proof of a successful disk write.
      const completed =
        envelope.status === "completed" &&
        result?.kind === "local_file" &&
        result?.status === "completed";
      const uncertain =
        ["timed_out", "device_disconnected", "permission_revoked"].includes(
          String(envelope.status),
        ) || result?.status === "uncertain";
      if (!completed && !uncertain) continue;
      receipts.set(id, {
        id,
        completed,
        path: typeof result?.path === "string" ? result.path : "",
        device:
          typeof envelope.device_id === "string" ? envelope.device_id : "",
      });
    } catch {
      /* Partial streamed output is not a receipt. */
    }
  }
  return [...receipts.values()];
}

export function LocalFileResults({ messages }: { messages: ChatMessage[] }) {
  const { t } = useTranslation();
  const desktop = useDesktopWorkspace();
  const native = Boolean(window.__octopRuntime);
  const previewScope = JSON.stringify([
    desktop.state?.owner_user_id,
    desktop.state?.path,
    desktop.state?.grant?.id,
    desktop.state?.grant?.version,
  ]);
  const local = new Map<
    string,
    {
      file: NonNullable<ChatMessage["localArtifacts"]>[number];
    }
  >();
  for (const m of messages)
    for (const file of m.localArtifacts ?? []) local.set(file.path, { file });
  const images = [...local.values()].filter(({ file }) =>
    isLocalPreviewImage(file.path),
  );
  const files = [...local.values()].filter(
    ({ file }) => !isLocalPreviewImage(file.path),
  );
  const openFileLocation = (
    file: NonNullable<ChatMessage["localArtifacts"]>[number],
  ) =>
    void requireDesktopRuntime()
      .reveal({ path: file.path, sha256: file.sha256 })
      .catch((err: unknown) =>
        message.error(err instanceof Error ? err.message : "无法打开文件位置"),
      );
  return (
    <Space direction="vertical" style={{ width: "100%" }}>
      {images.length > 0 && (
        <div className={styles.turnInset}>
          <div className={styles.toolMediaStrip}>
            <div className={styles.messageImages}>
              <Image.PreviewGroup key={previewScope}>
                {images.map(({ file }) => (
                  <Space
                    key={file.path + file.sha256}
                    direction="vertical"
                    size={4}
                  >
                    <LocalImagePreview file={file} scope={previewScope} />
                    {native && (
                      <Button
                        type="link"
                        size="small"
                        onClick={() => openFileLocation(file)}
                      >
                        打开文件位置
                      </Button>
                    )}
                  </Space>
                ))}
              </Image.PreviewGroup>
            </div>
          </div>
        </div>
      )}
      {files.map(({ file }) => (
        <Alert
          key={file.path + file.sha256}
          type="success"
          message={
            native ? "本地任务成果" : "成果保存在任务电脑，当前网页不能直接访问"
          }
          description={
            <>
              <Typography.Text copyable>{file.path}</Typography.Text>
              <div>
                {file.size} bytes · SHA-256 {file.sha256}
              </div>
              {native && (
                <Button onClick={() => openFileLocation(file)}>
                  打开文件位置
                </Button>
              )}
            </>
          }
        />
      ))}
      {localFileResults(messages).map((r) => (
        <Alert
          key={r.id}
          showIcon
          type={r.completed ? "success" : "warning"}
          message={t(
            r.completed ? "devices.savedLocal" : "devices.localUncertain",
          )}
          description={
            <>
              <div>{r.path}</div>
              <div>{r.device}</div>
              <Typography.Text copyable>{r.id}</Typography.Text>
              <div>{t("devices.localResultHint")}</div>
            </>
          }
        />
      ))}
    </Space>
  );
}
