import { useTranslation } from "react-i18next";
import {
  connectorsApi,
  type ConnectorCatalogEntry,
  type ConnectorInstance,
} from "../../../api/modules/connectors";
import { useAsyncResource } from "../../../hooks/useAsyncResource";

const EMPTY = {
  catalog: [] as ConnectorCatalogEntry[],
  instances: [] as ConnectorInstance[],
};

export function useConnectorInstances() {
  const { t } = useTranslation();

  const { data, loading, refresh } = useAsyncResource(
    EMPTY,
    async () => {
      const [cat, inst] = await Promise.all([
        connectorsApi.catalog(),
        connectorsApi.listInstances(),
      ]);
      return { catalog: cat ?? [], instances: inst ?? [] };
    },
    [],
    { errorFallback: t("connectors.loadFailed"), t, logLabel: "Connectors" },
  );

  return {
    catalog: data.catalog,
    instances: data.instances,
    loading,
    refresh,
  };
}
