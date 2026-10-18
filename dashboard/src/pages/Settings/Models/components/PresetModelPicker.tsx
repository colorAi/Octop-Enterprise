/**
 * Card-grid picker for preset initial models (name, hint, context, modalities).
 */
import { Button, Checkbox, Space, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { enrichWizardModel } from "../wizardModelMeta";
import type { ProviderPresetModel } from "../useProviders";
import { ModelMetaTags } from "../modelMeta";
import styles from "../index.module.less";

const { Text } = Typography;

interface PresetModelPickerProps {
  models: ProviderPresetModel[];
  value?: string[];
  onChange?: (ids: string[]) => void;
}

function ModelTileBody({ model }: { model: ProviderPresetModel }) {
  const { t } = useTranslation();
  const meta = enrichWizardModel(model, t);

  return (
    <div className={styles.presetModelTileBody}>
      <span className={styles.presetModelName}>{model.name}</span>
      {model.name !== model.id && (
        <span className={styles.presetModelId}>{model.id}</span>
      )}
      <span className={styles.presetModelDesc}>{meta.description}</span>
      <ModelMetaTags
        includeText
        input={meta.input}
        reasoning={meta.reasoning}
        context_window={meta.context_window}
        max_tokens={meta.max_tokens}
      />
    </div>
  );
}

export function PresetModelPicker({
  models,
  value = [],
  onChange,
}: PresetModelPickerProps) {
  const { t } = useTranslation();

  const handleSelectAll = () => {
    onChange?.(models.map((m) => m.id));
  };

  const handleDeselectAll = () => {
    onChange?.([]);
  };

  if (models.length === 0) {
    return (
      <div className={styles.presetModelEmpty}>{t("models.noModels")}</div>
    );
  }

  return (
    <div className={styles.presetModelPicker}>
      <div className={styles.presetModelPickerHeader}>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t("models.initialModelsHint")}
        </Text>
        <Space size={4}>
          <Button type="link" size="small" onClick={handleSelectAll}>
            {t("models.selectAllModels")}
          </Button>
          <Button type="link" size="small" onClick={handleDeselectAll}>
            {t("models.deselectAllModels")}
          </Button>
        </Space>
      </div>

      <Checkbox.Group
        className={styles.presetModelGrid}
        value={value}
        onChange={(ids) => onChange?.(ids as string[])}
      >
        {models.map((m) => (
          <Checkbox key={m.id} value={m.id} className={styles.presetModelTile}>
            <ModelTileBody model={m} />
          </Checkbox>
        ))}
      </Checkbox.Group>
    </div>
  );
}
