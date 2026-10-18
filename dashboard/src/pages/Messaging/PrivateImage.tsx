import { useEffect, useState } from "react";
import { Image, Spin } from "antd";
import { useTranslation } from "react-i18next";
import { communicationApi } from "../../api/modules/communication";

/** Authenticated bytes stay in an object URL only for this mounted message. */
export default function PrivateImage({
  asset,
  onLoad,
}: {
  asset: string;
  onLoad?: () => void;
}) {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    setUrl("");
    setFailed(false);
    void communicationApi
      .media(asset, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [asset]);
  if (failed) return <span>{t("communication.imageUnavailable")}</span>;
  return url ? (
    <Image
      onLoad={onLoad}
      src={url}
      alt={t("communication.image")}
      style={{ maxWidth: 260, maxHeight: 260, objectFit: "contain" }}
    />
  ) : (
    <Spin size="small" />
  );
}
