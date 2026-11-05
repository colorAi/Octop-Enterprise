import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "../../../api/desktopRuntime";
import LocalProjectFiles from "./LocalProjectFiles";
const p = vi.hoisted(() => ({
  state: {
    supported: true,
    current_project_id: "B",
    projects: { A: { name: "Project A" }, B: { name: "Project B" } },
    threads: { "chat-a": "A", "chat-b": "B" },
  },
}));
vi.mock("../../../context/DesktopProjectsContext", () => ({
  useDesktopProjects: () => p,
}));
afterEach(() => {
  delete window.__octopRuntime;
});
it("uses the open conversation's directory and discards late replies", async () => {
  let old: ((value: unknown) => void) | undefined;
  const files = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          old = resolve;
        }),
    )
    .mockResolvedValue({
      items: [{ path: "B.txt", name: "B.txt", is_dir: false }],
      truncated: false,
    });
  window.__octopRuntime = {
    projects: { files },
  } as unknown as DesktopRuntimeBridge;
  const { rerender } = render(<LocalProjectFiles threadId="chat-a" />);
  await waitFor(() =>
    expect(files).toHaveBeenCalledWith(
      expect.objectContaining({ thread_id: "chat-a", project_id: "A" }),
    ),
  );
  rerender(<LocalProjectFiles threadId="chat-b" />);
  await screen.findByText("B.txt");
  await act(async () => {
    old!({
      items: [{ path: "A.txt", name: "A.txt", is_dir: false }],
      truncated: false,
    });
  });
  expect(screen.queryByText("A.txt")).not.toBeInTheDocument();
  expect(screen.getByText("B.txt")).toBeInTheDocument();
});
