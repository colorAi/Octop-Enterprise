import { Empty } from "antd";
import { useTranslation } from "react-i18next";
import PageShell from "../../layouts/PageShell";

interface Props {
  title?: string;
  subtitle?: string;
}

/**
 * Temporary placeholder for admin pages pending sub-project ⑨.
 */
export default function AdminPlaceholder({ title, subtitle }: Props) {
  const { t } = useTranslation();
  return (
    <PageShell
      title={title || t("common.comingSoon", "Coming soon")}
      subtitle={subtitle}
    >
      <Empty
        description={t(
          "common.notImplemented",
          "This page is under construction.",
        )}
      />
    </PageShell>
  );
}
