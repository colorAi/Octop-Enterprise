import { expect, it } from "vitest";
import { navAllowed, userCan, userCanAny } from "./permissions";

it("keeps expert use separate from the expert management menu for assigned employees", () => {
  const employee = {
    role: "user",
    expert_access_mode: "assigned_only" as const,
    permissions: ["experts", "messaging"],
  };
  expect(userCan(employee, "experts")).toBe(false);
  expect(navAllowed(employee, "experts")).toBe(false);
  expect(userCanAny(employee, ["experts", "messaging"])).toBe(true);
  expect(userCan({ ...employee, role: "admin" }, "experts")).toBe(true);
  expect(
    userCan({ ...employee, expert_access_mode: "legacy" }, "experts"),
  ).toBe(true);
});
