import { useEffect, useState } from "react";
import { Alert } from "antd";
import { useTranslation } from "react-i18next";
import { communicationApi } from "../api/modules/communication";

export default function GroupExpertSyncBanner() {
  const { t } = useTranslation();
  const [syncing, setSyncing] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      if (document.visibilityState === "hidden") return;
      void communicationApi
        .expertAuthorizationStatus(controller.signal)
        .then((v) => {
          if (!controller.signal.aborted) setSyncing(v.syncing);
        })
        .catch(() => undefined);
    };
    refresh();
    const pending = () => setSyncing(true);
    window.addEventListener("octop:group-expert-syncing", pending);
    const timer = setInterval(refresh, 2000);
    return () => {
      controller.abort();
      clearInterval(timer);
      window.removeEventListener("octop:group-expert-syncing", pending);
    };
  }, []);
  return syncing ? (
    <Alert banner type="info" message={t("groupExperts.syncing")} />
  ) : null;
}
