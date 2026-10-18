import { App, Button, Input } from "antd";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { clientConfigApi } from "../../../api/modules/clientConfig";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import styles from "./UpdateConfig.module.less";

export default function ClientConfigPublish() {
  const user = useCurrentUser();
  const { t, i18n } = useTranslation();
  const { message } = App.useApp();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [publishedAt, setPublishedAt] = useState<number | null>(null);
  const isAdmin = user?.role === "admin";
  useEffect(() => {
    if (!isAdmin) return;
    let cancelled = false;
    void clientConfigApi
      .status()
      .then((status) => {
        if (!cancelled) setPublishedAt(status.published_at);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [isAdmin]);
  if (!isAdmin) return null;
  const publish = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const release = await clientConfigApi.publish(note.trim());
      setPublishedAt(release.published_at);
      setNote("");
      message.success(t("clientConfig.published"));
    } catch {
      message.error(t("clientConfig.publishFailed"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className={styles.panel} aria-label={t("clientConfig.adminTitle")}>
      <h3>{t("clientConfig.adminTitle")}</h3>
      <p>{t("clientConfig.adminDescription")}</p>
      <Input.TextArea
        aria-label={t("clientConfig.note")}
        placeholder={t("clientConfig.note")}
        value={note}
        onChange={(event) => setNote(event.target.value)}
        maxLength={500}
        rows={2}
      />
      <Button
        type="primary"
        loading={busy}
        onClick={() => void publish()}
        style={{ marginTop: 12 }}
      >
        {t("clientConfig.publish")}
      </Button>
      {publishedAt && (
        <p>
          {t("clientConfig.lastPublished", {
            time: new Date(publishedAt * 1000).toLocaleString(
              i18n.language === "zh" ? "zh-CN" : "en-US",
            ),
          })}
        </p>
      )}
    </section>
  );
}
