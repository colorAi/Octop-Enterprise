/**
 * Localised labels for built-in agent tools shown in chat message bubbles.
 * Labels are defined in backend ``src/octop/i18n/*.json`` (``tools.*``).
 */

import { useTranslation } from "react-i18next";

export function useToolDisplayNames(): (name?: string) => string {
  const { t } = useTranslation();

  return (name?: string) => {
    if (!name) return t("tools.unknown", "unknown");
    const key = `tools.${name}`;
    const translated = t(key);
    return translated === key ? name : translated;
  };
}

/** Resolve a display label when the stream chunk already carries ``display_name``. */
export function resolveToolLabel(
  name: string | undefined,
  displayName: string | undefined,
  translate: (name?: string) => string,
): string {
  if (displayName?.trim()) return displayName;
  return translate(name);
}
