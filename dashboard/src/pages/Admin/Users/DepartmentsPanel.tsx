import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
} from "antd";
import CompanyStructure from "./CompanyStructure";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { communicationApi } from "../../../api/modules/communication";
import type { ManagedGroup } from "../../../api/types/communication";
import { newClientMessageId } from "../../../utils/communicationId";
import { communicationErrorText } from "../../Messaging/communicationUi";
import { useCommunication } from "../../Messaging/hooks/CommunicationContext";
const org = communicationApi.organization;

function GroupManager({ id, close }: { id: string; close: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { state, client } = useCommunication();
  const joinRequest = useRef(newClientMessageId());
  const [group, setGroup] = useState<ManagedGroup>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [form] = Form.useForm<{
    name: string;
    announcement: string;
    mute_all: boolean;
  }>();
  const controller = useRef(new AbortController());
  const isMember = group?.members.some(
    (member) => member.user_id === state.profile?.user_id,
  );
  const load = useCallback(async () => {
    const signal = controller.current.signal;
    const value = await org.group(id, signal);
    if (!signal.aborted) {
      setGroup(value);
      form.setFieldsValue({
        name: value.name,
        announcement: value.announcement ?? "",
        mute_all: value.mute_all,
      });
    }
  }, [id, form]);
  useEffect(() => {
    controller.current = new AbortController();
    const current = controller.current;
    void load().catch((e) => {
      if (!current.signal.aborted) setError(communicationErrorText(e, t));
    });
    return () => current.abort();
  }, [load, t]);
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try {
      await action();
      await load();
    } catch (e) {
      if (!controller.current.signal.aborted)
        setError(communicationErrorText(e, t));
    } finally {
      if (!controller.current.signal.aborted) setBusy(false);
    }
  };
  return (
    <Drawer
      open
      onClose={close}
      title={group?.name || t("communication.groupDetails")}
      width={700}
    >
      {error && <Alert type="error" showIcon message={error} />}
      {group && state.profile && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message={t(
            isMember
              ? "communication.groupMemberEntryHint"
              : "communication.groupManagementOnlyHint",
          )}
          action={
            isMember ? (
              <Button
                onClick={() => {
                  close();
                  navigate(`/messages/${encodeURIComponent(id)}`);
                }}
              >
                {t("communication.openGroupChat")}
              </Button>
            ) : (
              <Button
                loading={busy}
                onClick={() =>
                  void run(async () => {
                    await communicationApi.groupMembers(
                      id,
                      {
                        request_id: joinRequest.current,
                        user_ids: [state.profile!.user_id],
                      },
                      false,
                      true,
                    );
                    await client.synchronize();
                  })
                }
              >
                {t("communication.joinGroup")}
              </Button>
            )
          }
        />
      )}
      <p>{t("communication.compulsoryGroup")}</p>
      <Form
        form={form}
        layout="vertical"
        onFinish={(values) =>
          void run(() => org.updateGroup(id, values, controller.current.signal))
        }
      >
        <Form.Item
          name="name"
          label={t("communication.name")}
          rules={[{ required: true, whitespace: true }]}
        >
          <Input maxLength={80} />
        </Form.Item>
        <Form.Item name="announcement" label={t("communication.announcement")}>
          <Input.TextArea maxLength={500} rows={3} />
        </Form.Item>
        <Form.Item
          name="mute_all"
          label={t("communication.muteAll")}
          valuePropName="checked"
        >
          <Switch disabled={busy} />
        </Form.Item>
        <Button
          htmlType="submit"
          type="primary"
          loading={busy}
          disabled={!group}
        >
          {t("communication.save")}
        </Button>
      </Form>
      <h3>{t("communication.members")}</h3>
      <Table
        size="small"
        rowKey="user_id"
        dataSource={group?.members ?? []}
        loading={!group && !error}
        scroll={{ x: 550 }}
        pagination={{ pageSize: 10 }}
        columns={[
          { title: t("communication.name"), dataIndex: "nickname" },
          {
            title: t("communication.membershipSource"),
            render: (_, member) => (
              <Tag>
                {t(
                  `communication.memberSources.${
                    member.membership_source || "automatic"
                  }`,
                )}
              </Tag>
            ),
          },
          {
            title: t("communication.role"),
            render: (_, member) =>
              member.role === "owner" ? (
                <Tag>{t("communication.roles.owner")}</Tag>
              ) : (
                <Select
                  size="small"
                  value={member.role}
                  disabled={busy}
                  options={[
                    { value: "member", label: t("communication.roles.member") },
                    { value: "admin", label: t("communication.roles.admin") },
                  ]}
                  onChange={(role) =>
                    void run(() =>
                      org.member(
                        id,
                        member.user_id,
                        { role },
                        controller.current.signal,
                      ),
                    )
                  }
                />
              ),
          },
          {
            title: t("communication.actions"),
            render: (_, member) => {
              const muted =
                !!member.mute_until &&
                Date.parse(member.mute_until) > Date.now();
              return member.role !== "owner" ? (
                <Space>
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() =>
                      void run(() =>
                        org.member(
                          id,
                          member.user_id,
                          {
                            mute_until: muted
                              ? null
                              : new Date(Date.now() + 3600000).toISOString(),
                          },
                          controller.current.signal,
                        ),
                      )
                    }
                  >
                    {t(
                      muted
                        ? "communication.unmuteMember"
                        : "communication.muteHour",
                    )}
                  </Button>
                  <Button
                    size="small"
                    disabled={busy}
                    onClick={() =>
                      Modal.confirm({
                        title: t("communication.transferOwner"),
                        content: member.nickname,
                        okText: t("communication.confirm"),
                        cancelText: t("communication.cancel"),
                        onOk: () =>
                          run(() =>
                            org.owner(
                              id,
                              member.user_id,
                              controller.current.signal,
                            ),
                          ),
                      })
                    }
                  >
                    {t("communication.transferOwner")}
                  </Button>
                </Space>
              ) : null;
            },
          },
        ]}
      />
    </Drawer>
  );
}

export default function DepartmentsPanel() {
  return (
    <CompanyStructure
      renderGroup={(id, close) => (
        <GroupManager key={id} id={id} close={close} />
      )}
    />
  );
}
