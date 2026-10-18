import { expect, it } from "vitest";
import type {
  Department,
  CommunicationUser,
} from "../../api/types/communication";
import {
  directoryTree,
  departmentPeople,
  departmentPath,
} from "./directoryTree";

const department = (id: string, parent: string | null = null): Department => ({
  department_id: id,
  name: id,
  parent_id: parent,
  sort_order: 0,
  enabled: true,
  conversation_id: null,
});
const departments = [
  department("Company"),
  department("Engineering", "Company"),
  department("Platform", "Engineering"),
  department("Empty", "Company"),
];
const people: CommunicationUser[] = [
  { user_id: "a", nickname: "Alice", department_id: "Engineering" },
  { user_id: "b", nickname: "Bob", department_id: "Platform" },
  { user_id: "c", nickname: "Carol", department_id: null },
];

it("keeps empty nested departments, counts descendants, and groups unassigned colleagues", () => {
  const tree = directoryTree(departments, people);
  expect(tree[0].count).toBe(2);
  expect(
    tree[0].children?.find((n) => n.department?.name === "Empty")?.count,
  ).toBe(0);
  expect(tree[1].key).toBe("unassigned");
  expect(tree[1].count).toBe(1);
  expect(
    departmentPeople("Engineering", departments, people).map((p) => p.user_id),
  ).toEqual(["a", "b"]);
});
it("searches people through their ancestors while keeping full population counts", () => {
  const tree = directoryTree(departments, people, "Bob");
  expect(tree).toHaveLength(1);
  expect(tree[0].count).toBe(2);
  expect(
    tree[0].children?.[0].children?.[0].children?.[0].person?.user_id,
  ).toBe("b");
  expect(
    directoryTree(departments, people, "Empty")[0].children?.[0].count,
  ).toBe(0);
});
it("preserves enabled branches with unavailable parents and resolves department breadcrumbs", () => {
  expect(
    directoryTree([department("Child", "Disabled")], []).map(
      (n) => n.department?.name,
    ),
  ).toEqual(["Child"]);
  expect(departmentPath("Platform", departments).map((d) => d.name)).toEqual([
    "Company",
    "Engineering",
    "Platform",
  ]);
});

it("excludes administrators from the directory and finds employees by their position", () => {
  const withTitles = [
    ...people.map((person) => ({
      ...person,
      job_title: person.user_id === "b" ? "经理" : null,
    })),
    {
      user_id: "admin",
      nickname: "Administrator",
      department_id: "Platform",
      is_admin: true,
    },
  ];
  const tree = directoryTree(departments, withTitles);
  expect(tree[0].count).toBe(2);
  expect(
    departmentPeople("Company", departments, withTitles).map((p) => p.user_id),
  ).toEqual(["a", "b"]);
  expect(
    JSON.stringify(directoryTree(departments, withTitles, "经理")),
  ).toContain('"user_id":"b"');
  expect(directoryTree(departments, withTitles, "Administrator")).toEqual([]);
});
