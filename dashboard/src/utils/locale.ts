import i18n, { ensureLocaleBundle } from "../i18n";
import {
  normalizeUiLocale,
  resolveInitialLocale,
  storeUiLocale,
  syncDocumentLang,
  type UiLocale,
} from "./localePrefs";

export type { UiLocale };
export {
  detectBrowserLocale,
  normalizeUiLocale,
  readStoredUiLocale,
  resolveInitialLocale,
  storeUiLocale,
  syncDocumentLang,
  UI_LOCALE_STORAGE_KEY,
} from "./localePrefs";

/** Apply server-stored locale to the dashboard i18n instance. */
export async function applyUserLocale(
  raw: string | null | undefined,
): Promise<UiLocale> {
  const lang = normalizeUiLocale(raw);
  storeUiLocale(lang);
  await ensureLocaleBundle(lang);
  if (i18n.language !== lang) {
    await i18n.changeLanguage(lang);
  }
  syncDocumentLang(lang);
  return lang;
}

/** Guest surfaces (login / post-logout): stored preference or browser locale. */
export async function applyGuestLocale(): Promise<UiLocale> {
  const lang = resolveInitialLocale();
  storeUiLocale(lang);
  await ensureLocaleBundle(lang);
  if (i18n.language !== lang) {
    await i18n.changeLanguage(lang);
  }
  syncDocumentLang(lang);
  return lang;
}
