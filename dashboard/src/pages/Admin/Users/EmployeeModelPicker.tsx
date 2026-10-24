import { useEffect, useState } from "react";
import { Alert, Button, Checkbox, Empty, Input, Space, Spin, Tag } from "antd";
import { useTranslation } from "react-i18next";
import {
  usersApi,
  type EmployeeModelAccess,
  type EmployeeModelSelection,
} from "../../../api/modules/users";
import { apiErrorMessage } from "../../../utils/apiError";
import styles from "./expertAssignments.module.less";

export default function EmployeeModelPicker({
  userId,
  disabled = false,
  onChange,
}: {
  userId: number;
  disabled?: boolean;
  onChange: (selection: EmployeeModelSelection | null) => void;
}) {
  const { t } = useTranslation();
  const [data, setData] = useState<EmployeeModelAccess | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setSelected([]);
    setSearch("");
    setError("");
    onChange(null);
    void usersApi.models(userId, controller.signal).then(
      (result) => {
        if (controller.signal.aborted) return;
        setData(result);
        setSelected(result.model_refs);
      },
      (err) => {
        if (!controller.signal.aborted)
          setError(apiErrorMessage(err, t("employeeModels.loadFailed"), t));
      },
    );
    return () => controller.abort();
  }, [userId, attempt, onChange, t]);
  const change = (refs: string[]) => {
    setSelected(refs);
    if (!data) return;
    const same =
      JSON.stringify([...refs].sort()) ===
      JSON.stringify([...data.model_refs].sort());
    onChange(
      same ? null : { model_refs: refs, expected_revision: data.revision },
    );
  };
  const missing = selected.filter(
    (ref) => !data?.items.some((m) => m.ref === ref),
  );
  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert type="info" showIcon message={t("employeeModels.hint")} />
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
      ) : (
        <>
          <Input.Search
            aria-label={t("employeeModels.search")}
            placeholder={t("employeeModels.search")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            allowClear
          />
          <Space>
            <Button
              size="small"
              disabled={disabled || !data.items.length}
              onClick={() => change(data.items.map((model) => model.ref))}
            >
              {t("employeeResources.selectAll")}
            </Button>
            <Button
              size="small"
              disabled={disabled || !selected.length}
              onClick={() => change([])}
            >
              {t("expertAccess.clear")}
            </Button>
            <span className={styles.hint}>
              {t("employeeModels.selected", { count: selected.length })}
            </span>
          </Space>
          {!data.items.length && (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={t("employeeModels.empty")}
            />
          )}
          {missing.map((ref) => (
            <div className={styles.candidate} key={ref}>
              <Tag>{t("expertAccess.unavailable")}</Tag>
              <span>{ref}</span>
              <Button
                size="small"
                danger
                disabled={disabled}
                onClick={() => change(selected.filter((item) => item !== ref))}
              >
                {t("expertAccess.removeUnavailable")}
              </Button>
            </div>
          ))}
          <div className={styles.candidates}>
            {data.items
              .filter((model) =>
                `${model.provider_name} ${model.name} ${model.model}`
                  .toLowerCase()
                  .includes(search.toLowerCase()),
              )
              .map((model) => (
                <Checkbox
                  key={model.ref}
                  className={styles.candidate}
                  aria-label={`${model.provider_name} / ${model.name}`}
                  checked={selected.includes(model.ref)}
                  disabled={disabled}
                  onChange={(event) =>
                    change(
                      event.target.checked
                        ? [...selected, model.ref]
                        : selected.filter((ref) => ref !== model.ref),
                    )
                  }
                >
                  <span className={styles.optionBody}>
                    <span>
                      <strong>{model.name}</strong>
                      <span className={styles.hint}>{model.ref}</span>
                    </span>
                  </span>
                </Checkbox>
              ))}
          </div>
          {!selected.length && (
            <Alert
              type="warning"
              showIcon
              message={t("employeeModels.noneAssigned")}
            />
          )}
        </>
      )}
    </Space>
  );
}
