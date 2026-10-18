import { afterEach, describe, expect, it, vi } from "vitest";
import {
  detectBrowserLocale,
  readStoredUiLocale,
  resolveInitialLocale,
  speechLocaleFromUi,
  storeUiLocale,
  syncDocumentLang,
  UI_LOCALE_STORAGE_KEY,
} from "./localePrefs";

describe("localePrefs", () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("detectBrowserLocale prefers zh when browser lists Chinese first", () => {
    vi.stubGlobal("navigator", {
      language: "en-US",
      languages: ["zh-CN", "en-US"],
    });
    expect(detectBrowserLocale()).toBe("zh");
  });

  it("detectBrowserLocale prefers en when browser lists English first", () => {
    vi.stubGlobal("navigator", {
      language: "zh-CN",
      languages: ["en-US", "zh-CN"],
    });
    expect(detectBrowserLocale()).toBe("en");
  });

  it("resolveInitialLocale uses stored preference over browser", () => {
    vi.stubGlobal("navigator", {
      language: "en-US",
      languages: ["en-US"],
    });
    storeUiLocale("zh");
    expect(resolveInitialLocale()).toBe("zh");
    expect(readStoredUiLocale()).toBe("zh");
    localStorage.removeItem(UI_LOCALE_STORAGE_KEY);
    expect(resolveInitialLocale()).toBe("en");
  });

  it("speechLocaleFromUi maps UI locale to STT BCP-47 tags", () => {
    expect(speechLocaleFromUi("zh")).toBe("zh-CN");
    expect(speechLocaleFromUi("zh-CN")).toBe("zh-CN");
    expect(speechLocaleFromUi("en")).toBe("en-US");
    expect(speechLocaleFromUi("en-US")).toBe("en-US");
    expect(speechLocaleFromUi(null)).toBe("zh-CN");
  });

  it("synchronizes native branding before the full Wails runtime is available", () => {
    for (const platform of ["wails", "mac", "windows"] as const) {
      const messages: string[] = [];
      const handler = {
        postMessage(message: string) {
          expect(this).toBe(handler);
          messages.push(message);
        },
      };
      vi.stubGlobal(
        "_wails",
        platform === "wails"
          ? { invoke: (message: string) => messages.push(message) }
          : undefined,
      );
      vi.stubGlobal(
        "webkit",
        platform === "mac"
          ? { messageHandlers: { external: handler } }
          : undefined,
      );
      vi.stubGlobal(
        "chrome",
        platform === "windows" ? { webview: handler } : undefined,
      );
      syncDocumentLang("en");
      expect(document.documentElement.lang).toBe("en");
      syncDocumentLang("zh");
      expect(document.documentElement.lang).toBe("zh-CN");
      expect(messages).toEqual([
        "wails:event:emit:desktop:locale:en",
        "wails:event:emit:desktop:locale:zh",
      ]);
    }
  });

  it("keeps browser language switching available when native delivery fails", () => {
    vi.stubGlobal("_wails", {
      invoke: () => {
        throw new Error("window closed");
      },
    });
    expect(() => syncDocumentLang("en")).not.toThrow();
    expect(document.documentElement.lang).toBe("en");
  });
});
