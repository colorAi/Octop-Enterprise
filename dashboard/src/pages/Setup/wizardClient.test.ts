import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const request = vi.hoisted(() => vi.fn());
vi.mock("../../api/request", () => ({ request }));
import { resolveSetupProbeToken, wizardSession } from "./wizardClient";

describe("setup probe credentials", () => {
  beforeEach(() => {
    sessionStorage.clear();
    request.mockReset();
  });
  afterEach(() => sessionStorage.clear());
  it("uses the original valid wizard token", async () => {
    wizardSession.saveToken("initial-token");
    request.mockResolvedValue({ valid: true });
    expect(await resolveSetupProbeToken()).toBe("initial-token");
    expect(request).toHaveBeenCalledOnce();
  });
  it("uses the signed-in admin after the wizard token expires", async () => {
    wizardSession.saveToken("expired-token");
    wizardSession.saveSetupJwt("admin-jwt");
    request.mockResolvedValue({ valid: false });
    expect(await resolveSetupProbeToken()).toBe("admin-jwt");
    expect(request).toHaveBeenCalledOnce();
  });
  it("does not mint anonymous tokens when no credential remains", async () => {
    expect(await resolveSetupProbeToken()).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });
});
