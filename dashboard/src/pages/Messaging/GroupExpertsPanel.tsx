import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Avatar,
  Button,
  List,
  Popconfirm,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
} from "antd";
import { Bot } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  communicationApi,
  communicationError,
} from "../../api/modules/communication";
import type { GroupExperts } from "../../api/types/communication";
import { newClientMessageId } from "../../utils/communicationId";
import { communicationErrorText } from "./communicationUi";

export function ExpertAvatar({
  url,
  size = 32,
}: {
  url?: string;
  size?: number;
}) {
  return (
    <Avatar size={size} src={url} icon={<Bot size={Math.round(size / 2)} />} />
  );
}

export default function GroupExpertsPanel({
  group,
  organization,
}: {
  group: string;
  organization: boolean;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<GroupExperts>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [selection, setSelection] = useState<string[]>([]);
  const [invite, setInvite] = useState<string>();
  const requests = useRef(
    new Map<string, { id: string; action: (id: string) => Promise<unknown> }>(),
  );
  const alive = useRef(true);
  const loadRevision = useRef(0);
  const selectionRevision = useRef(0);
  const selectionDirty = useRef(false);
  const load = useCallback(async () => {
    const revision = ++loadRevision.current;
    const value = await communicationApi.groupExperts(group, organization);
    if (alive.current && revision === loadRevision.current) {
      setData(value);
      if (!selectionDirty.current) {
        setSelection(
          value.scope_sources.map(
            (v) => `${v.source_agent_id}:${v.source_row_id}`,
          ),
        );
      }
    }
  }, [group, organization]);
  useEffect(() => {
    alive.current = true;
    void load().catch((e: unknown) => {
      if (alive.current) setError(communicationErrorText(e, t));
    });
    return () => {
      alive.current = false;
    };
  }, [load, t]);
  useEffect(() => {
    if (!data?.syncing) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      await load().catch(() => undefined);
      if (!stopped) timer = setTimeout(refresh, 2000);
    };
    timer = setTimeout(refresh, 2000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [data?.syncing, load]);
  const run = async (
    operation: string,
    action: (id: string) => Promise<unknown>,
    savedSelectionRevision?: number,
  ) => {
    const key = `${group}:${organization}:${operation}`;
    const pending = requests.current.get(key) ?? {
      id: newClientMessageId(),
      action,
    };
    requests.current.set(key, pending);
    setBusy(true);
    setError("");
    try {
      await pending.action(pending.id);
      requests.current.delete(key);
      if (savedSelectionRevision === selectionRevision.current) {
        selectionDirty.current = false;
      }
      await load();
    } catch (e) {
      if (alive.current) setError(communicationErrorText(e, t));
      const failure = communicationError(e);
      // Network/5xx failures may already have committed: retain their exact ID
      // and payload. A definitive rejection permits a new request after reload.
      if (failure.status >= 400 && failure.status < 500 && !failure.retryable) {
        requests.current.delete(key);
        await load().catch(() => undefined);
      }
    } finally {
      if (alive.current) setBusy(false);
    }
  };
  const active = data?.items.filter((v) => v.state === "active") ?? [];
  const choices =
    data?.candidates.filter(
      (v) => !active.some((b) => b.source_agent_id === v.source_agent_id),
    ) ?? [];
  return (
    <section aria-label={t("groupExperts.title")} style={{ margin: "20px 0" }}>
      <h3>
        {t("groupExperts.title")} <Tag>{active.length}/3</Tag>
      </h3>
      <p>{t("groupExperts.boundary")}</p>
      {error && <Alert type="warning" showIcon message={error} />}
      {!data ? (
        !error && <Spin />
      ) : (
        <>
          {data.syncing && (
            <Alert type="info" showIcon message={t("groupExperts.syncing")} />
          )}
          {!data.ready && (
            <Alert
              type="info"
              message={t("groupExperts.protocolUnavailable")}
            />
          )}
          {data.can_manage && !data.syncing && data.candidates.length === 0 && (
            <Alert
              type="info"
              showIcon
              message={t(
                !data.can_configure
                  ? "groupExperts.noScopeCandidates"
                  : data.group_kind === "department"
                  ? "groupExperts.noDepartmentCandidates"
                  : "groupExperts.noPublishedCandidates",
              )}
            />
          )}
          {!data.experts_enabled && (
            <Alert
              type="info"
              showIcon
              message={t("groupExperts.disabledHint")}
            />
          )}
          {data.can_configure && (
            <Space style={{ margin: "12px 0" }}>
              <Switch
                aria-label={t("groupExperts.enable")}
                checked={data.experts_enabled}
                disabled={busy || (!data.ready && !data.experts_enabled)}
                onChange={(enabled) =>
                  void run(`enable:${enabled}`, (request_id) =>
                    communicationApi.enableExperts(group, {
                      request_id,
                      enabled,
                    }),
                  )
                }
              />
              {t("groupExperts.enable")}
            </Space>
          )}
          <List
            dataSource={active}
            locale={{ emptyText: t("groupExperts.empty") }}
            renderItem={(expert) => (
              <List.Item
                actions={
                  data.can_manage
                    ? [
                        <Popconfirm
                          key="remove"
                          title={t("groupExperts.confirmRemove")}
                          onConfirm={() =>
                            run(`remove:${expert.binding_id}`, (request_id) =>
                              communicationApi.removeExpert(
                                group,
                                expert.binding_id,
                                { request_id, generation: expert.generation },
                                organization,
                              ),
                            )
                          }
                        >
                          <Button type="text" danger disabled={busy}>
                            {t("communication.remove")}
                          </Button>
                        </Popconfirm>,
                      ]
                    : []
                }
              >
                <List.Item.Meta
                  avatar={<ExpertAvatar url={expert.avatar.url} />}
                  title={expert.display_name}
                  description={t("groupExperts.expert")}
                />
              </List.Item>
            )}
          />
          {data.can_manage && (
            <Space.Compact style={{ width: "100%", margin: "12px 0" }}>
              <Select
                aria-label={t("groupExperts.choose")}
                value={invite}
                onChange={setInvite}
                showSearch
                optionFilterProp="label"
                style={{ flex: 1 }}
                placeholder={t("groupExperts.choose")}
                options={choices.map((v) => ({
                  value: `${v.source_agent_id}:${v.source_row_id}`,
                  label: v.display_name,
                }))}
              />
              <Button
                disabled={
                  busy ||
                  active.length >= 3 ||
                  !invite ||
                  !data.ready ||
                  !data.experts_enabled
                }
                onClick={() => {
                  const source = choices.find(
                    (v) => `${v.source_agent_id}:${v.source_row_id}` === invite,
                  );
                  if (source)
                    void run(`invite:${invite}`, (request_id) =>
                      communicationApi.inviteExpert(
                        group,
                        {
                          request_id,
                          source_agent_id: source.source_agent_id,
                          source_row_id: source.source_row_id,
                          revision: data.scope_revision,
                        },
                        organization,
                      ),
                    );
                }}
              >
                {t("groupExperts.invite")}
              </Button>
            </Space.Compact>
          )}
          {data.can_configure && (
            <div>
              <h4>{t("groupExperts.scope")}</h4>
              <p>{t("groupExperts.scopeHint")}</p>
              <Select
                aria-label={t("groupExperts.scope")}
                mode="multiple"
                style={{ width: "100%" }}
                value={selection}
                onChange={(value) => {
                  selectionDirty.current = true;
                  selectionRevision.current++;
                  setSelection(value);
                }}
                optionFilterProp="label"
                options={data.candidates.map((v) => ({
                  value: `${v.source_agent_id}:${v.source_row_id}`,
                  label: v.display_name,
                }))}
              />
              <Button
                style={{ marginTop: 8 }}
                disabled={busy || !data.ready}
                onClick={() =>
                  void run(
                    `scope:${selection.join(",")}`,
                    (request_id) =>
                      communicationApi.expertScope(group, {
                        request_id,
                        revision: data.scope_revision,
                        sources: data.candidates
                          .filter((v) =>
                            selection.includes(
                              `${v.source_agent_id}:${v.source_row_id}`,
                            ),
                          )
                          .map(({ source_agent_id, source_row_id }) => ({
                            source_agent_id,
                            source_row_id,
                          })),
                      }),
                    selectionRevision.current,
                  )
                }
              >
                {t("communication.save")}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
