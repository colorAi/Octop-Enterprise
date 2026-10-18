import { describe, expect, it } from "vitest";
import {
  SIDEBAR_GROUPED_NAV_KEYS,
  SIDEBAR_NAV_KEYS,
  buildNavSections,
  isGroupedNavKey,
} from "./sidebarNav";
import type { OctopUser } from "../api/modules/auth";

const adminUser = {
  id: 1,
  username: "admin",
  role: "admin",
  permissions: ["*"],
} as OctopUser;

describe("sidebarNav", () => {
  it("gives employees a separate workspace entry only in the desktop client", () => {
    const employee = {
      id: 2,
      role: "user",
      permissions: [],
    } as unknown as OctopUser;
    const desktop = buildNavSections(employee, { desktopAvailable: true });
    expect(
      desktop.find((section) => section.id === "settings")?.items[0],
    ).toMatchObject({
      key: "local-workspace",
      path: "/settings/workspace",
      labelKey: "devices.workspaceTitle",
    });
    expect(
      buildNavSections(employee)
        .flatMap((section) => section.items)
        .some((item) => item.key === "local-workspace"),
    ).toBe(false);
  });
  it("marks catalog keys as grouped", () => {
    for (const key of SIDEBAR_GROUPED_NAV_KEYS) {
      expect(isGroupedNavKey(key)).toBe(true);
    }
    expect(isGroupedNavKey("chat")).toBe(false);
    expect(isGroupedNavKey("experts")).toBe(false);
  });

  it("places grouped keys only under sections with groupKey", () => {
    const sections = buildNavSections(adminUser, { mobileEnabled: true });
    const flatKeys = new Set(
      sections
        .filter((s) => !s.groupKey)
        .flatMap((s) => s.items.map((i) => i.key)),
    );
    const groupedKeys = new Set(
      sections
        .filter((s) => s.groupKey)
        .flatMap((s) => s.items.map((i) => i.key)),
    );
    for (const key of groupedKeys) {
      expect(isGroupedNavKey(key)).toBe(true);
      expect(flatKeys.has(key)).toBe(false);
    }
    for (const key of flatKeys) {
      expect(isGroupedNavKey(key)).toBe(false);
    }
  });

  it("covers every catalog key from the admin nav", () => {
    const keys = buildNavSections(adminUser, {
      mobileEnabled: true,
      desktopAvailable: true,
    }).flatMap((section) => section.items.map((item) => item.key));
    expect(new Set(keys)).toEqual(
      new Set(SIDEBAR_NAV_KEYS.filter((key) => key !== "messages")),
    );
    const grouped = buildNavSections(adminUser, { mobileEnabled: true }).filter(
      (section) => section.id,
    );
    expect(grouped.map((section) => section.id)).toEqual([
      "settings",
      "control",
      "admin",
    ]);
  });
});

it("gates communication navigation and allows department-only administrators", () => {
  const user = { ...adminUser, role: "user", permissions: ["departments"] };
  const keys = buildNavSections(user).flatMap((s) => s.items.map((i) => i.key));
  expect(keys).not.toContain("messages");
  expect(keys).not.toContain("contacts");
  expect(keys).toContain("admin-users");
  const granted = buildNavSections(
    { ...user, permissions: ["messaging", "contacts"] },
    { unread: 12, companyName: "示例科技有限公司" },
  ).flatMap((s) => s.items);
  expect(granted.slice(0, 2).map((i) => i.key)).toEqual(["contacts", "chat"]);
  expect(granted.find((i) => i.key === "contacts")).toMatchObject({
    path: "/contacts",
    labelKey: "nav.company",
    label: "示例科技有限公司",
    unread: 12,
  });
  expect(granted.find((i) => i.key === "chat")?.unread).toBeUndefined();
  const messagingOnly = buildNavSections({
    ...user,
    permissions: ["messaging"],
  }).flatMap((s) => s.items);
  expect(messagingOnly[0]).toMatchObject({
    key: "contacts",
    path: "/messages",
  });
  const contactsOnly = buildNavSections(
    { ...user, permissions: ["contacts"] },
    { unread: 12 },
  ).flatMap((s) => s.items);
  expect(contactsOnly[0]).toMatchObject({
    key: "contacts",
    path: "/contacts",
    unread: undefined,
  });
});
