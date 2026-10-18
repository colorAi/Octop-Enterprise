export type UiLocale = "zh" | "en";

export const UI_LOCALE_STORAGE_KEY = "octop:ui-locale";

/** Map browser language tags to a supported dashboard locale. */
export function detectBrowserLocale(): UiLocale {
  if (typeof navigator === "undefined") return "en";

  const candidates =
    navigator.languages?.length > 0
      ? navigator.languages
      : [navigator.language];

  for (const raw of candidates) {
    const lang = raw?.toLowerCase() ?? "";
    if (lang.startsWith("zh")) return "zh";
    if (lang.startsWith("en")) return "en";
  }

  const primary = navigator.language?.toLowerCase() ?? "";
  if (primary.startsWith("zh")) return "zh";
  if (primary.startsWith("en")) return "en";

  return "en";
}

export function normalizeUiLocale(raw: string | null | undefined): UiLocale {
  if (!raw) return "zh";
  return raw.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function readStoredUiLocale(): UiLocale | null {
  try {
    const raw = localStorage.getItem(UI_LOCALE_STORAGE_KEY);
    if (raw === "zh" || raw === "en") return raw;
  } catch {
    // localStorage unavailable
  }
  return null;
}

export function storeUiLocale(locale: UiLocale): void {
  try {
    localStorage.setItem(UI_LOCALE_STORAGE_KEY, locale);
  } catch {
    // quota / disabled
  }
}

/** Stored user preference wins; otherwise follow the browser. */
export function resolveInitialLocale(): UiLocale {
  return readStoredUiLocale() ?? detectBrowserLocale();
}

export function syncDocumentLang(locale: UiLocale): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale === "zh" ? "zh-CN" : "en";
  // HTTP pages can apply their locale before the full Wails runtime loads.
  // Use the platform's early bridge so native titles and the next login agree.
  if (typeof window === "undefined") return;
  const bridge = window as unknown as {
    _wails?: { invoke?: (message: string) => void };
    webkit?: {
      messageHandlers?: {
        external?: { postMessage: (message: string) => void };
      };
    };
    chrome?: { webview?: { postMessage: (message: string) => void } };
  };
  const message = `wails:event:emit:desktop:locale:${locale}`;
  try {
    if (bridge._wails?.invoke) bridge._wails.invoke(message);
    else if (bridge.webkit?.messageHandlers?.external)
      bridge.webkit.messageHandlers.external.postMessage(message);
    else bridge.chrome?.webview?.postMessage(message);
  } catch {
    // A closing native window must not prevent the UI changing language.
  }
}

/** BCP-47 tag for STT / SpeechRecognition from dashboard UI locale. */
export function speechLocaleFromUi(locale: string | null | undefined): string {
  return normalizeUiLocale(locale) === "zh" ? "zh-CN" : "en-US";
}
