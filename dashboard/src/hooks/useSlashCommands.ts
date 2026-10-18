import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { slashApi, type SlashCommandSpec } from "../api/modules/slash";
import { buildFallbackSlashCommands } from "../utils/slashFallbackCommands";

export function useSlashCommands(origin = "ui") {
  const { i18n, t } = useTranslation();
  const fallbackCommands = useMemo(() => buildFallbackSlashCommands(t), [t]);
  const [commands, setCommands] =
    useState<SlashCommandSpec[]>(fallbackCommands);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setCommands(fallbackCommands);
  }, [fallbackCommands]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    slashApi
      .listCommands(origin)
      .then((res) => {
        if (!cancelled && res.commands.length > 0) {
          setCommands(res.commands);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCommands(fallbackCommands);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [origin, fallbackCommands]);

  const labelFor = (spec: SlashCommandSpec) =>
    i18n.language.startsWith("zh")
      ? spec.label_zh || spec.label_en
      : spec.label_en || spec.label_zh;

  return { commands, loading, labelFor };
}
