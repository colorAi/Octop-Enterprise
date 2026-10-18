import { useTranslation } from "react-i18next";
import styles from "../index.module.less";

interface ChatComposerChromeProps {
  sessionUsageLabel: string | null;
}

export default function ChatComposerChrome({
  sessionUsageLabel,
}: ChatComposerChromeProps) {
  const { t } = useTranslation();

  if (!sessionUsageLabel) {
    return null;
  }

  return (
    <div className={styles.runUsageBar}>
      {t("chatUsage.sessionTotal")}: {sessionUsageLabel}
    </div>
  );
}
