import { useEffect, useState } from "react";
import { Alert, Button, Drawer, Input, Select, Space, Table, Tag } from "antd";
import { useTranslation } from "react-i18next";
import { communicationApi } from "../../../api/modules/communication";
import type { GroupInfo, GroupKind } from "../../../api/types/communication";
import { communicationErrorText } from "../../Messaging/communicationUi";

export default function CompanyGroupDirectory({
  close,
  manage,
}: {
  close: () => void;
  manage: (id: string) => void;
}) {
  const { t } = useTranslation();
  const [groups, setGroups] = useState<GroupInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState<GroupKind | "all">("all");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    void communicationApi
      .managedGroups(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setGroups(value.items);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(communicationErrorText(e, t));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [revision, t]);
  return (
    <Drawer
      open
      width={760}
      title={t("communication.manageGroups")}
      onClose={close}
    >
      <p>{t("groupExperts.directoryHint")}</p>
      {error && <Alert showIcon type="error" message={error} />}
      <Space wrap style={{ marginBottom: 16 }}>
        <Input.Search
          allowClear
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder={t("communication.searchGroupChats")}
        />
        <Select
          aria-label={t("communication.groupKindFilter")}
          value={kind}
          onChange={setKind}
          options={["all", "company", "department", "project"].map((value) => ({
            value,
            label: t(`communication.groupKinds.${value}`),
          }))}
        />
        <Button loading={loading} onClick={() => setRevision((v) => v + 1)}>
          {t("common.refresh")}
        </Button>
      </Space>
      <Table
        size="small"
        rowKey="conversation_id"
        loading={loading}
        dataSource={groups.filter(
          (g) =>
            (kind === "all" || g.group_kind === kind) &&
            g.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()),
        )}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: t("communication.name"), dataIndex: "name" },
          {
            title: t("communication.groupKindFilter"),
            render: (_, g) => (
              <Tag>
                {t(`communication.groupKinds.${g.group_kind || "department"}`)}
              </Tag>
            ),
          },
          { title: t("communication.members"), dataIndex: "member_count" },
          {
            title: t("communication.actions"),
            render: (_, g) => (
              <Button
                disabled={!g.conversation_id || g.status !== "active"}
                onClick={() => manage(g.conversation_id!)}
              >
                {t("communication.manageGroups")}
              </Button>
            ),
          },
        ]}
      />
    </Drawer>
  );
}
