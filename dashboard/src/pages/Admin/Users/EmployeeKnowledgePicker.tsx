import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Space, Spin, Tag } from "antd";
import { BookOpen } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  usersApi,
  type EmployeeKnowledgeAccess,
} from "../../../api/modules/users";
import { apiErrorMessage } from "../../../utils/apiError";
import styles from "./expertAssignments.module.less";

export default function EmployeeKnowledgePicker({
  userId,
  disabled = false,
  onChange,
}: {
  userId: number;
  disabled?: boolean;
  onChange: (ids: string[] | null) => void;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<EmployeeKnowledgeAccess | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError("");
    onChange(null);
    void usersApi.knowledgeBases(userId, controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return;
        setData(result);
        const ids = result.knowledge_base_ids.filter((id) =>
          result.items.some((base) => base.id === id),
        );
        setSelected(ids);
        onChange(ids);
      },
      (err) => {
        if (!controller.signal.aborted)
          setError(apiErrorMessage(err, t("knowledgeBases.loadFailed"), t));
      },
    );
    return () => controller.abort();
  }, [userId, attempt, onChange, t]);

  const change = (ids: string[]) => {
    setSelected(ids);
    onChange(ids);
  };
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon message={t("employeeKnowledge.hint")} />
      {error ? (
        <Alert
          type="error"
          showIcon
          message={error}
          action={
            <Button onClick={() => setAttempt((value) => value + 1)}>
              {t("common.retry")}
            </Button>
          }
        />
      ) : !data ? (
        <Spin />
      ) : data.items.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t("employeeKnowledge.empty")}
        />
      ) : (
        <>
          <Input.Search
            aria-label={t("employeeKnowledge.search")}
            placeholder={t("employeeKnowledge.search")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            allowClear
          />
          <Space>
            <Button
              size="small"
              disabled={disabled}
              onClick={() =>
                change(
                  data.items
                    .filter((base) => !base.shared)
                    .map((base) => base.id),
                )
              }
            >
              {t("employeeResources.selectAll")}
            </Button>
            <Button size="small" disabled={disabled} onClick={() => change([])}>
              {t("expertAccess.clear")}
            </Button>
          </Space>
          <div className={styles.candidates}>
            {data.items
              .filter((base) =>
                `${base.name} ${base.description}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((base) => (
                <Checkbox
                  key={base.id}
                  className={styles.candidate}
                  aria-label={base.name}
                  checked={base.shared || selected.includes(base.id)}
                  disabled={disabled || base.shared}
                  onChange={(event) =>
                    change(
                      event.target.checked
                        ? [...selected, base.id]
                        : selected.filter((id) => id !== base.id),
                    )
                  }
                >
                  <span className={styles.optionBody}>
                    <BookOpen size={19} />
                    <span>
                      <strong>{base.name}</strong>
                      {base.shared && (
                        <Tag>{t("employeeKnowledge.shared")}</Tag>
                      )}
                      {base.description && (
                        <span className={styles.hint}>{base.description}</span>
                      )}
                    </span>
                  </span>
                </Checkbox>
              ))}
          </div>
        </>
      )}
    </Space>
  );
}
