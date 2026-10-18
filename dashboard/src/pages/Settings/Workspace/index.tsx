import { Alert, Card } from "antd";
import { useTranslation } from "react-i18next";
import PageShell from "../../../layouts/PageShell";
import LocalWorkspaceSettings from "../../../components/LocalWorkspaceSettings";
import { useDesktopWorkspace } from "../../../context/DesktopWorkspaceContext";

export default function LocalWorkspacePage() {
  const { t } = useTranslation();
  const desktop = useDesktopWorkspace();
  return (
    <PageShell title={t("devices.workspaceTitle")}>
      {desktop.available ? (
        <Card style={{ maxWidth: 840 }}>
          <LocalWorkspaceSettings />
        </Card>
      ) : (
        <Alert type="info" message={t("devices.filePreview")} />
      )}
    </PageShell>
  );
}
