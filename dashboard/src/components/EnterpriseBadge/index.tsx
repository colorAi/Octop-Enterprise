import { useTranslation } from "react-i18next";
import styles from "./index.module.less";

export default function EnterpriseBadge({
  isMobile = false,
}: {
  isMobile?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <span
      className={`${styles.badge} ${isMobile ? styles.mobile : ""}`}
      title={t("app.productName")}
    >
      {t("app.edition")}
    </span>
  );
}
