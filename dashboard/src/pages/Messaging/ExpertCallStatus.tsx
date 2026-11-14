import { useTranslation } from "react-i18next";
import type { GroupExpertCall } from "../../api/types/communication";

export default function ExpertCallStatus({ call }: { call?: GroupExpertCall }) {
  const { t } = useTranslation();
  // Durable sync events and history reloads are the single source of state.
  const current = call;
  if (!current) return null;
  return (
    <div
      role="status"
      style={{ fontSize: 12, color: "var(--fn-text-secondary)", marginTop: 6 }}
    >
      {t(`groupExperts.states.${current.call_state}`)}
      {current.reason_code && (
        <>
          {" "}
          ·{" "}
          {t(`communication.errors.${current.reason_code}`, {
            defaultValue: t("groupExperts.failed"),
          })}
        </>
      )}
      {current.call_state === "unknown" && (
        <p>{t("groupExperts.unknownHint")}</p>
      )}
    </div>
  );
}
