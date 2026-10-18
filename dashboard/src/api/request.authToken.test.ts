import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../i18n", () => ({
  default: { language: "zh" },
}));

describe("auth token remember / session-only", () => {
  let mod: typeof import("./request");

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    sessionStorage.clear();
    mod = await import("./request");
  });

  it("defaults to a remembered (persistent) token in localStorage", () => {
    mod.setAuthToken("tok");
    expect(mod.getAuthToken()).toBe("tok");
    expect(mod.isSessionOnlyAuth()).toBe(false);
    expect(localStorage.getItem("auth_token")).toBe("tok");
    expect(sessionStorage.getItem("auth_token")).toBeNull();
  });

  it("stores a session-only token in sessionStorage", () => {
    mod.setAuthToken("tok", false);
    expect(mod.getAuthToken()).toBe("tok");
    expect(mod.isSessionOnlyAuth()).toBe(true);
    expect(sessionStorage.getItem("auth_token")).toBe("tok");
    expect(localStorage.getItem("auth_token")).toBeNull();
  });

  it("clears the other store when switching remember mode", () => {
    mod.setAuthToken("a", false);
    mod.setAuthToken("b", true);
    expect(localStorage.getItem("auth_token")).toBe("b");
    expect(sessionStorage.getItem("auth_token")).toBeNull();
    expect(mod.isSessionOnlyAuth()).toBe(false);
  });

  it("preserves session-only mode when renewing the access token", () => {
    mod.setAuthToken("old", false);
    const response = new Response(null, {
      headers: { "X-Octop-Access-Token": "renewed" },
    });
    mod.applyRenewedAccessToken(response, "old");
    expect(mod.getAuthToken()).toBe("renewed");
    expect(mod.isSessionOnlyAuth()).toBe(true);
    expect(sessionStorage.getItem("auth_token")).toBe("renewed");
  });

  it("reads and writes the SSO remember preference", () => {
    expect(mod.getRememberLoginPreference()).toBe(true);
    mod.setRememberLoginPreference(false);
    expect(mod.getRememberLoginPreference()).toBe(false);
  });

  it("clearAuthToken removes both stores", () => {
    mod.setAuthToken("tok", false);
    mod.clearAuthToken();
    expect(mod.getAuthToken()).toBe("");
    expect(sessionStorage.getItem("auth_token")).toBeNull();
  });

  it.each(["logout", "another account"])(
    "ignores a late renewal after %s",
    async (action) => {
      mod.setAuthToken("old-session");
      let complete!: (response: Response) => void;
      vi.stubGlobal(
        "fetch",
        vi.fn(
          () =>
            new Promise<Response>((resolve) => {
              complete = resolve;
            }),
        ),
      );
      const pending = mod.request("/im/contacts");
      if (action === "logout") mod.clearAuthToken();
      else mod.setAuthToken("new-session");
      complete(
        new Response("{}", {
          headers: {
            "content-type": "application/json",
            "X-Octop-Access-Token": "old-renewed",
          },
        }),
      );
      await pending;
      expect(mod.getAuthToken()).toBe(action === "logout" ? "" : "new-session");
      vi.unstubAllGlobals();
    },
  );
});
