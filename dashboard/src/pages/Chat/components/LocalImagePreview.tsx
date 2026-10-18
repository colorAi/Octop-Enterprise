import { useEffect, useState } from "react";
import { Button, Image, Space, Typography } from "antd";
import type { LocalArtifact } from "@/api/desktopRuntime";
import styles from "../index.module.less";

export function isLocalPreviewImage(path: string): boolean {
  return /\.(png|jpe?g|gif|webp)$/i.test(path);
}

/** A native-generated PNG thumbnail is the only accepted image source. */
export function isNativePreviewData(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 8 * 1024 * 1024 &&
    /^data:image\/png;base64,[a-zA-Z0-9+/]+={0,2}$/.test(value)
  );
}

export function LocalImagePreview({
  file,
  scope,
}: {
  file: LocalArtifact;
  scope: string;
}) {
  const bridge = window.__octopRuntime;
  const supported = Boolean(bridge?.previewImage);
  const [revision, setRevision] = useState(0);
  const key = JSON.stringify([scope, file.path, file.sha256, revision]);
  const [state, setState] = useState<{
    key: string;
    data?: string;
    error?: string;
  } | null>(null);
  useEffect(() => {
    if (!bridge?.previewImage) return;
    let cancelled = false;
    void bridge
      .previewImage({ path: file.path, sha256: file.sha256 })
      .then((result) => {
        if (!isNativePreviewData(result.data_url))
          throw new Error("本机返回了无效的图片预览");
        if (!cancelled) setState({ key, data: result.data_url });
      })
      .catch((error: unknown) => {
        if (!cancelled)
          setState({
            key,
            error:
              error instanceof Error ? error.message : "无法读取本机图片预览",
          });
      });
    return () => {
      cancelled = true;
    };
  }, [bridge, file.path, file.sha256, key]);

  if (!bridge)
    return (
      <Typography.Text type="secondary">
        图片保存在任务电脑，当前网页不能直接预览。
      </Typography.Text>
    );
  if (!supported)
    return (
      <Typography.Text type="secondary">
        网页已更新；当前客户端尚不支持本地图片预览，请安装更新后的客户端。可先打开文件位置查看。
      </Typography.Text>
    );
  const current = state?.key === key ? state : null;
  const name = file.path.split(/[\\/]/).at(-1) || "图片";
  // Native reads and lazy image decoding can finish while older messages are
  // on screen. Keep the same thumbnail space in every state so rows above the
  // reader cannot grow underneath an otherwise stationary scroll position.
  return (
    <div className={styles.localImagePreviewFrame}>
      {current?.data ? (
        <Image
          src={current.data}
          alt={`本地图片预览：${name}`}
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          className={styles.toolMediaImage}
        />
      ) : current?.error ? (
        <Space wrap>
          <Typography.Text type="secondary">{current.error}</Typography.Text>
          <Button
            size="small"
            onClick={() => setRevision((value) => value + 1)}
          >
            重试预览
          </Button>
        </Space>
      ) : (
        <Typography.Text type="secondary">正在读取本机图片…</Typography.Text>
      )}
    </div>
  );
}
