import type {
  CommunicationUser,
  Department,
} from "../../api/types/communication";

export interface DirectoryNode {
  key: string;
  department?: Department;
  person?: CommunicationUser;
  count: number;
  children?: DirectoryNode[];
}

export function departmentPath(
  id: string | null | undefined,
  departments: Department[],
) {
  const byId = new Map(departments.map((d) => [d.department_id, d]));
  const path: Department[] = [],
    seen = new Set<string>();
  while (id && byId.has(id) && !seen.has(id)) {
    seen.add(id);
    const department = byId.get(id)!;
    path.unshift(department);
    id = department.parent_id;
  }
  return path;
}

export function departmentPeople(
  id: string,
  departments: Department[],
  people: CommunicationUser[],
) {
  return people.filter(
    (p) =>
      !p.is_admin &&
      departmentPath(p.department_id, departments).some(
        (d) => d.department_id === id,
      ),
  );
}

/** Counts include descendants; an empty department remains a selectable node. */
export function directoryTree(
  departments: Department[],
  people: CommunicationUser[],
  search = "",
) {
  const nodes = new Map<string, DirectoryNode>();
  const roots: DirectoryNode[] = [];
  const sorted = [...departments].sort(
    (a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name),
  );
  for (const d of sorted)
    nodes.set(d.department_id, {
      key: `department:${d.department_id}`,
      department: d,
      count: 0,
      children: [],
    });
  for (const d of sorted) {
    const node = nodes.get(d.department_id)!;
    const parent = d.parent_id && nodes.get(d.parent_id);
    // Treat an unavailable ancestor as a root, preserving enabled children.
    if (parent && d.parent_id !== d.department_id) parent.children!.push(node);
    else roots.push(node);
  }
  const unassigned: DirectoryNode = {
    key: "unassigned",
    count: 0,
    children: [],
  };
  for (const person of people
    .filter((p) => !p.is_admin)
    .sort((a, b) => a.nickname.localeCompare(b.nickname))) {
    const parent =
      (person.department_id && nodes.get(person.department_id)) || unassigned;
    parent.children!.push({
      key: `person:${person.user_id}`,
      person,
      count: 1,
    });
  }
  if (unassigned.children!.length) roots.push(unassigned);
  const count = (node: DirectoryNode): number => {
    node.count = node.person
      ? 1
      : (node.children ?? []).reduce((sum, child) => sum + count(child), 0);
    return node.count;
  };
  roots.forEach(count);
  const query = search.trim().toLocaleLowerCase();
  if (!query) return roots;
  const filter = (node: DirectoryNode): DirectoryNode | null => {
    if (
      (node.person
        ? `${node.person.nickname} ${node.person.job_title ?? ""}`
        : node.department?.name ?? ""
      )
        .toLocaleLowerCase()
        .includes(query)
    )
      return node;
    const children = (node.children ?? [])
      .map(filter)
      .filter((n): n is DirectoryNode => n !== null);
    return children.length ? { ...node, children } : null;
  };
  return roots.map(filter).filter((n): n is DirectoryNode => n !== null);
}
