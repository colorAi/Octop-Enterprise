import { lazy, Suspense, useState } from "react";
import { Forward } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { userCan } from "../../utils/permissions";
import type { ForwardSource } from "./forwardContent";
import styles from "./ForwardMessageButton.module.less";

const ForwardDialog = lazy(() => import("./ForwardDialog"));

export default function ForwardMessageButton({
  source,
  disabled = false,
  className = styles.button,
}: {
  source: ForwardSource;
  disabled?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const user = useCurrentUser();
  const [open, setOpen] = useState(false);
  if (!userCan(user, "messaging")) return null;
  return (
    <>
      <button
        type="button"
        className={className}
        disabled={disabled}
        title={t("communication.forward")}
        aria-label={t("communication.forward")}
        onClick={() => setOpen(true)}
      >
        <Forward size={14} />
        {t("communication.forward")}
      </button>
      {open && (
        <Suspense fallback={null}>
          <ForwardDialog source={source} onClose={() => setOpen(false)} />
        </Suspense>
      )}
    </>
  );
}
