import { describe, expect, it } from "vitest";
import { hasProcessRestarted } from "./useServiceRestart";

describe("hasProcessRestarted", () => {
  it("returns true when started_at changes", () => {
    expect(hasProcessRestarted(100, 200)).toBe(true);
  });

  it("returns false when started_at is missing or unchanged", () => {
    expect(hasProcessRestarted(undefined, 200)).toBe(false);
    expect(hasProcessRestarted(100, undefined)).toBe(false);
    expect(hasProcessRestarted(100, 100)).toBe(false);
  });
});
