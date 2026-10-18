import { describe, expect, it } from "vitest";
import type { OctopUser } from "../api/modules/auth";
import { buildNavSections } from "./sidebarNav";
import {
  createGroup,
  deleteGroup,
  editorFromCatalog,
  layoutFromEditor,
  placeItem,
  sectionsFromLayout,
  visibleEditorGroups,
} from "./sidebarNavLayout";

const adminUser = {
  id: 1,
  username: "admin",
  role: "admin",
  permissions: ["*"],
} as OctopUser;

const limitedUser = {
  id: 2,
  username: "ada",
  role: "user",
  permissions: [],
} as OctopUser;

function catalog() {
  return buildNavSections(adminUser, { mobileEnabled: true });
}

describe("sidebarNavLayout", () => {
  it("adds the desktop workspace entry to existing saved settings menus", () => {
    const desktop = buildNavSections(limitedUser, { desktopAvailable: true });
    const editor = editorFromCatalog(desktop, {
      groups: [{ id: "settings" }],
      items: [{ key: "chat" }, { key: "personalization", group: "settings" }],
    });
    expect(editor.itemsByGroup.settings).toContain("local-workspace");
    expect(editor.hidden).not.toContain("local-workspace");
  });
  it("keeps the default catalog when no layout is saved", () => {
    const sections = catalog();
    expect(sectionsFromLayout(sections, null)).toBe(sections);
    const editor = editorFromCatalog(sections, null);
    expect(editor.ungrouped).toEqual([
      "contacts",
      "chat",
      "experts",
      "tasks",
      "token-usage",
    ]);
    expect(editor.groups.map((group) => group.id)).toEqual([
      "settings",
      "control",
      "admin",
    ]);
    expect(editor.hidden).toEqual([]);
  });

  it("omits empty groups and hidden items from the sidebar", () => {
    const sections = catalog();
    const visibleKeys = new Set([
      "contacts",
      "chat",
      "personalization",
      "workbench",
    ]);
    const applied = sectionsFromLayout(sections, {
      groups: [
        { id: "settings", name: "配置" },
        { id: "control" },
        { id: "c_emptygrp1", name: "空组" },
      ],
      items: [
        { key: "chat" },
        { key: "personalization", group: "settings" },
        { key: "token-usage", hidden: true },
        { key: "workbench", group: "control" },
        ...sections
          .flatMap((section) => section.items)
          .filter(
            (item) => !visibleKeys.has(item.key) && item.key !== "token-usage",
          )
          .map((item) => ({ key: item.key, hidden: true })),
      ],
    });
    expect(applied.map((section) => section.id ?? "front")).toEqual([
      "front",
      "settings",
      "control",
    ]);
    expect(applied[0]?.items.map((item) => item.key)).toEqual([
      "contacts",
      "chat",
    ]);
    expect(applied[1]?.title).toBe("配置");
    expect(applied[1]?.items.map((item) => item.key)).toEqual([
      "personalization",
    ]);
    expect(applied[2]?.groupKey).toBe("nav.control");
  });

  it("adds newly available entries in their default sections of existing layouts", () => {
    const editor = editorFromCatalog(catalog(), {
      groups: [{ id: "settings" }],
      items: [{ key: "chat" }],
    });
    expect(editor.ungrouped).toEqual([
      "contacts",
      "chat",
      "experts",
      "tasks",
      "token-usage",
    ]);
    expect(editor.hidden).not.toContain("experts");
    expect(editor.hidden).not.toContain("personalization");
    expect(editor.itemsByGroup.settings).toContain("personalization");
    expect(editor.itemsByGroup.control).toContain("workbench");
    expect(
      sectionsFromLayout(catalog(), {
        groups: [{ id: "settings" }],
        items: [{ key: "chat" }],
      })
        .flatMap((section) => section.items.map((item) => item.key))
        .includes("experts"),
    ).toBe(true);
  });

  it("shows newly granted channels and browser with the employee's saved menu", () => {
    const layout = {
      groups: [{ id: "settings" }],
      items: [
        { key: "contacts" },
        { key: "chat" },
        { key: "experts" },
        { key: "tasks" },
        { key: "token-usage" },
        { key: "personalization", group: "settings" },
        { key: "bridge", hidden: true },
      ],
    };
    const permissions = ["contacts", "experts", "tasks", "token_usage"];
    const before = buildNavSections({ ...limitedUser, permissions });
    const after = buildNavSections({
      ...limitedUser,
      permissions: [...permissions, "browser", "channels"],
    });
    expect(
      sectionsFromLayout(before, layout).flatMap((section) =>
        section.items.map((item) => item.key),
      ),
    ).not.toContain("workbench");
    const displayed = sectionsFromLayout(after, layout);
    expect(
      displayed
        .find((section) => section.id === "settings")
        ?.items.map((item) => item.key),
    ).toEqual(["personalization", "channels"]);
    expect(
      displayed
        .find((section) => section.id === "control")
        ?.items.map((item) => item.key),
    ).toEqual(["workbench"]);
    expect(
      displayed.flatMap((section) => section.items.map((item) => item.key)),
    ).not.toContain("bridge");
    // Revoking access still removes entries even if a saved placement remains.
    const saved = layoutFromEditor(editorFromCatalog(after, layout));
    expect(
      sectionsFromLayout(before, saved).flatMap((section) =>
        section.items.map((item) => item.key),
      ),
    ).not.toContain("channels");
  });

  it("moves a deleted group's items to the front of the ungrouped list", () => {
    const editor = editorFromCatalog(catalog(), null);
    const next = deleteGroup(editor, "settings");
    expect(next.groups.map((group) => group.id)).toEqual(["control", "admin"]);
    expect(next.ungrouped.slice(0, 2)).toEqual(["personalization", "channels"]);
    expect(next.ungrouped).toContain("chat");
  });

  it("places an item before another and can hide it", () => {
    const editor = editorFromCatalog(catalog(), null);
    const moved = placeItem(editor, "tasks", { kind: "ungrouped" }, "chat");
    expect(moved.ungrouped.slice(0, 3)).toEqual(["contacts", "tasks", "chat"]);
    const hidden = placeItem(moved, "tasks", { kind: "hidden" }, null);
    expect(hidden.ungrouped).not.toContain("tasks");
    expect(hidden.hidden).toContain("tasks");
    const layout = layoutFromEditor(hidden);
    expect(layout.items.find((item) => item.key === "tasks")?.hidden).toBe(
      true,
    );
  });

  it("omits nav items and built-in groups the user cannot access", () => {
    const sections = buildNavSections(limitedUser, { mobileEnabled: false });
    const editor = editorFromCatalog(sections, {
      groups: [
        { id: "settings" },
        { id: "admin" },
        { id: "c_aabbccdd", name: "常用" },
      ],
      items: [
        { key: "chat" },
        { key: "admin-users", group: "admin" },
        { key: "channels", group: "settings" },
        { key: "personalization", group: "settings" },
      ],
    });
    const shown = [
      ...editor.ungrouped,
      ...Object.values(editor.itemsByGroup).flat(),
      ...editor.hidden,
    ];
    expect(shown).not.toContain("admin-users");
    expect(shown).not.toContain("channels");
    expect(shown).toContain("chat");
    expect(shown).toContain("personalization");
    expect(
      visibleEditorGroups(editor, sections).map((group) => group.id),
    ).toEqual(["settings", "c_aabbccdd"]);
  });

  it("keeps a new empty group only in the editor", () => {
    const editor = createGroup(
      editorFromCatalog(catalog(), null),
      "c_aabbccdd",
      "常用",
    );
    expect(editor.groups.at(-1)?.name).toBe("常用");
    const applied = sectionsFromLayout(catalog(), layoutFromEditor(editor));
    expect(applied.some((section) => section.id === "c_aabbccdd")).toBe(false);
  });
});

it("keeps the retired messages menu out and respects explicitly hidden company entries", () => {
  const old = { groups: [], items: [{ key: "chat" }] };
  expect(editorFromCatalog(catalog(), old).ungrouped).toEqual([
    "contacts",
    "chat",
    "experts",
    "tasks",
    "token-usage",
  ]);
  const hidden = {
    groups: [],
    items: [
      { key: "chat" },
      { key: "messages", hidden: true },
      { key: "contacts", hidden: true },
    ],
  };
  const result = sectionsFromLayout(catalog(), hidden).flatMap((s) =>
    s.items.map((i) => i.key),
  );
  expect(result).not.toContain("messages");
  expect(result).not.toContain("contacts");
});
