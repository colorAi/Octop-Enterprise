import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import PageShell from "../../../layouts/PageShell";
import { useDesktopWorkspace } from "../../../context/DesktopWorkspaceContext";
import LocalWorkspaceSettings from "../../../components/LocalWorkspaceSettings";
import {
  devicesApi,
  type DeviceList,
  type PairingCode,
} from "../../../api/modules/devices";

export default function DevicesPage() {
  const { t } = useTranslation();
  const desktop = useDesktopWorkspace();
  const [data, setData] = useState<DeviceList | null>(null);
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState("");
  const [now, setNow] = useState(Date.now());
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let loading = false;
    const refresh = async () => {
      if (loading) return;
      loading = true;
      try {
        const next = await devicesApi.list(controller.signal);
        if (!controller.signal.aborted) setData(next);
      } catch {
        if (!controller.signal.aborted) {
          setError(t("devices.loadError"));
          setData(null);
        }
      } finally {
        loading = false;
      }
    };
    void refresh();
    const poll = window.setInterval(() => void refresh(), 10000);
    return () => {
      controller.abort();
      window.clearInterval(poll);
    };
  }, [revision, t]);
  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(tick);
  }, []);
  useEffect(() => {
    if (pairing && pairing.expires_at * 1000 <= now) setPairing(null);
  }, [now, pairing]);

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    setResult("");
    try {
      await action();
      setRevision((r) => r + 1);
    } catch {
      setError(t("devices.actionError"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell title={t("devices.title")} subtitle={t("devices.subtitle")}>
      <Space
        direction="vertical"
        size="middle"
        style={{ width: "100%", maxWidth: 840 }}
      >
        {error && <Alert type="error" message={error} />}
        {result && <Alert type="info" message={result} />}
        {data === null && !error && <Spin />}
        <Button
          onClick={() => {
            setError("");
            setRevision((r) => r + 1);
          }}
        >
          {t("devices.refresh")}
        </Button>
        {data && !data.enabled && (
          <Alert type="info" message={t("devices.disabled")} />
        )}
        {data?.enabled && (
          <>
            {desktop.available ? (
              <Card>
                <LocalWorkspaceSettings />
              </Card>
            ) : (
              <>
                <Alert
                  type="info"
                  message={t("devices.filePreview")}
                  description={t("devices.sessionHint")}
                />
                <Card title={t("devices.pairTitle")}>
                  <Typography.Paragraph>
                    {t("devices.pairHint")}
                  </Typography.Paragraph>
                  <Button
                    type="primary"
                    loading={busy}
                    onClick={() =>
                      void run(async () => {
                        setPairing(await devicesApi.pair());
                      })
                    }
                  >
                    {t("devices.createCode")}
                  </Button>
                  {pairing && (
                    <div style={{ marginTop: 16 }}>
                      <Input.Password
                        aria-label={t("devices.code")}
                        readOnly
                        value={pairing.code}
                        autoComplete="off"
                      />
                      <Typography.Paragraph copyable={{ text: pairing.code }}>
                        {t("devices.copyCode")}
                      </Typography.Paragraph>
                      <Typography.Text type="secondary">
                        {t("devices.expires", {
                          seconds: Math.max(
                            0,
                            Math.ceil((pairing.expires_at * 1000 - now) / 1000),
                          ),
                        })}
                      </Typography.Text>
                    </div>
                  )}
                </Card>
              </>
            )}
            {!data.devices.length && <Empty description={t("devices.empty")} />}
            {data.devices.map((device) => {
              const revoked = device.revoked_at !== null;
              const expired = device.expires_at * 1000 <= now;
              const state = revoked
                ? "revoked"
                : expired
                ? "expired"
                : device.online
                ? "online"
                : "offline";
              return (
                <Card
                  key={device.device_id}
                  title={device.name}
                  extra={
                    <Tag color={state === "online" ? "green" : "default"}>
                      {t(`devices.${state}`)}
                    </Tag>
                  }
                >
                  <Typography.Paragraph>
                    {device.platform === "darwin" ? "macOS" : "Windows"} ·{" "}
                    {device.device_id}
                  </Typography.Paragraph>
                  <Space>
                    <Button
                      disabled={busy || state !== "online"}
                      onClick={() =>
                        void run(async () => {
                          const probe = await devicesApi.probe(
                            device.device_id,
                          );
                          setResult(t(`devices.probe_${probe.status}`));
                        })
                      }
                    >
                      {t("devices.probe")}
                    </Button>
                    <Button
                      danger
                      disabled={busy || revoked}
                      onClick={() =>
                        void run(() => devicesApi.revoke(device.device_id))
                      }
                    >
                      {t("devices.revoke")}
                    </Button>
                  </Space>
                </Card>
              );
            })}
          </>
        )}
      </Space>
    </PageShell>
  );
}
