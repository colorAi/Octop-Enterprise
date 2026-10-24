import { App } from "antd";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  clientConfigApi,
  type ClientConfigStatus,
} from "../../api/modules/clientConfig";
import { isCompanyDesktop } from "../../api/desktopRuntime";
import { useCurrentUser, useSetCurrentUser } from "../../hooks/useCurrentUser";
import { hasActiveChatWork } from "../../pages/Chat/hooks/chatStore";
import { parseApiError } from "../../utils/apiError";
import {
  CLIENT_CONFIG_CHECK_EVENT,
  permissionsSignature,
  prepareClientConfigRefresh,
  reloadClientConfig,
} from "../../utils/clientConfigUpdate";
import styles from "./index.module.less";

/** Refresh company settings inside the running app; never install or restart. */
export default function ClientConfigUpdatePrompt() {
  const user = useCurrentUser();
  const setUser = useSetCurrentUser();
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [notice, setNotice] = useState<ClientConfigStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const applying = useRef(false);
  const activeScope = useRef<number | null>(null);
  const userRef = useRef(user);
  userRef.current = user;
  const userId = user?.id;

  useEffect(() => {
    if (userId === undefined) return;
    activeScope.current = userId;
    let cancelled = false;
    let inFlight = false;
    let pending = false;
    let revision: string | null = null;
    const check = async () => {
      if (cancelled) return;
      if (inFlight) {
        pending = true;
        return;
      }
      inFlight = true;
      try {
        const next = await clientConfigApi.status();
        if (
          cancelled ||
          next.user_id !== userId ||
          userRef.current?.id !== userId
        )
          return;
        const previous = userRef.current;
        const rightsChanged =
          permissionsSignature(previous.role, previous.permissions) !==
            permissionsSignature(next.role, next.permissions) ||
          (next.expert_access_mode !== undefined &&
            (previous.expert_access_mode ?? "legacy") !==
              next.expert_access_mode);
        // Server enforcement is already current. Reflect revocations immediately,
        // even when the employee postpones refreshing feature configuration.
        if (rightsChanged)
          setUser((prev) =>
            prev?.id === userId
              ? {
                  ...prev,
                  role: next.role,
                  permissions: next.permissions,
                  expert_access_mode:
                    next.expert_access_mode ?? prev.expert_access_mode,
                }
              : prev,
          );
        if (
          (revision !== null && revision !== next.revision) ||
          (revision === null && (rightsChanged || next.resource_update_pending))
        )
          setNotice(next);
        revision = next.revision;
      } catch {
        // Offline or older servers keep their existing configuration and session.
      } finally {
        inFlight = false;
        if (pending && !cancelled) {
          pending = false;
          void check();
        }
      }
    };
    const onVisible = () => {
      if (document.visibilityState !== "hidden") void check();
    };
    setNotice(null);
    void check();
    let timer: number;
    const schedule = () => {
      timer = window.setTimeout(
        () => {
          onVisible();
          schedule();
        },
        25_000 + Math.floor(Math.random() * 10_000),
      );
    };
    schedule();
    window.addEventListener(CLIENT_CONFIG_CHECK_EVENT, onVisible);
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      activeScope.current = null;
      window.clearTimeout(timer);
      window.removeEventListener(CLIENT_CONFIG_CHECK_EVENT, onVisible);
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // Same-account locale/profile changes must not discard pending notices.
  }, [userId, setUser]);

  if (!notice || !user || notice.user_id !== user.id) return null;

  const apply = async () => {
    if (applying.current || activeScope.current !== user.id) return;
    const ready = () => !hasActiveChatWork() && prepareClientConfigRefresh();
    if (!ready()) {
      message.info(t("clientConfig.busy"));
      return;
    }
    applying.current = true;
    setBusy(true);
    try {
      if (isCompanyDesktop()) {
        if (!window.__octopRuntime)
          throw new Error("native status unavailable");
        const runtime = window.__octopRuntime;
        const status = runtime.refreshStatus
          ? await runtime.refreshStatus()
          : await runtime.status();
        if (activeScope.current !== user.id) return;
        // The legacy field also contains completed tasks and restored receipts.
        // Those survive app restarts but do not represent work in progress.
        if (
          status.active_tasks.some(
            (task) =>
              !task.status ||
              !["completed", "failed", "cancelled", "unknown"].includes(
                task.status,
              ),
          )
        ) {
          message.info(t("clientConfig.busy"));
          return;
        }
      }
      const latest = await clientConfigApi.status();
      if (
        activeScope.current !== user.id ||
        latest.user_id !== user.id ||
        userRef.current?.id !== user.id
      )
        return;
      if (!ready()) {
        message.info(t("clientConfig.busy"));
        return;
      }
      const applied = await clientConfigApi.apply();
      if (
        activeScope.current !== user.id ||
        applied.user_id !== user.id ||
        userRef.current?.id !== user.id
      )
        return;
      if (!ready()) {
        message.info(t("clientConfig.busy"));
        return;
      }
      // Reload only the webview at its existing URL. Tokens and saved drafts
      // stay in the same browser origin; the native app/runtime remain running.
      reloadClientConfig();
    } catch (error) {
      if (activeScope.current === user.id) {
        const reason = parseApiError(error)?.details?.reason;
        if (reason === "client_config_busy")
          message.info(t("clientConfig.busy"));
        else
          message.error(
            t(
              reason === "employee_resources_changed"
                ? "clientConfig.changed"
                : "clientConfig.failed",
            ),
          );
      }
    } finally {
      applying.current = false;
      if (activeScope.current === user.id) setBusy(false);
    }
  };

  return (
    <section className={styles.banner} role="status" aria-live="polite">
      <div className={styles.copy}>
        <strong>{t("clientConfig.available")}</strong>
        <span>{notice.message || t("clientConfig.description")}</span>
      </div>
      <button
        type="button"
        className={styles.primary}
        disabled={busy}
        onClick={() => void apply()}
      >
        {t(busy ? "clientConfig.applying" : "clientConfig.apply")}
      </button>
      <button
        type="button"
        className={styles.later}
        disabled={busy}
        onClick={() => setNotice(null)}
      >
        {t("clientConfig.later")}
      </button>
    </section>
  );
}
