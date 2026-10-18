import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { localFileResults, LocalFileResults } from "./LocalFileResults";
import type { ChatMessage } from "../hooks/useChat";
const id = "a".repeat(32);
afterEach(() => {
  delete window.__octopRuntime;
});
function message(status: string, result?: object): ChatMessage {
  return {
    id: "tool",
    role: "tool",
    content: "",
    status: "done",
    timestamp: 0,
    toolData: {
      name: "local_write_file",
      output: JSON.stringify({
        execution_location: "local",
        call_id: id,
        status,
        device_id: "pc",
        result,
      }),
    },
  };
}
describe("local result receipts", () => {
  it("requires a successful disk receipt before saying saved", () => {
    expect(
      localFileResults([
        message("completed", {
          kind: "local_file",
          status: "completed",
          path: "report.docx",
          operation_id: id,
        }),
      ])[0]?.completed,
    ).toBe(true);
    expect(
      localFileResults([
        message("completed", { kind: "local_error", status: "failed" }),
      ]),
    ).toEqual([]);
    expect(localFileResults([message("completed")])).toEqual([]);
  });
  it("keeps disconnect and uncertain receipts pending reconciliation", () => {
    expect(
      localFileResults([message("device_disconnected")])[0]?.completed,
    ).toBe(false);
    expect(
      localFileResults([
        message("completed", {
          kind: "local_file",
          status: "uncertain",
          operation_id: id,
        }),
      ])[0]?.completed,
    ).toBe(false);
  });
  it("uses the reconciled original operation id and deduplicates it", () => {
    const done = message("completed", {
      kind: "local_file",
      status: "completed",
      operation_id: id,
    });
    done.toolData!.name = "local_operation_status";
    expect(localFileResults([message("timed_out"), done])).toHaveLength(1);
    expect(localFileResults([message("timed_out"), done])[0]?.completed).toBe(
      true,
    );
  });
  it("does not turn a server file into a local receipt", () => {
    const m = message("completed", { kind: "local_file", status: "completed" });
    m.toolData!.name = "write_file";
    expect(localFileResults([m])).toEqual([]);
  });
});

it("deduplicates persisted local paths and never offers native file opening in a browser", () => {
  const item: ChatMessage = {
    id: "receipt",
    role: "assistant",
    content: "",
    timestamp: 0,
    localArtifacts: [
      { path: "/work/local-result.pdf", size: 1, sha256: "a".repeat(64) },
    ],
    localArtifactDeviceId: "other-device",
  };
  render(
    <LocalFileResults messages={[item, { ...item, id: "receipt-copy" }]} />,
  );
  expect(screen.getAllByText("/work/local-result.pdf")).toHaveLength(1);
  expect(
    screen.getByText("成果保存在任务电脑，当前网页不能直接访问"),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "打开文件位置" }),
  ).not.toBeInTheDocument();
});

it("checks native file availability instead of treating a rotated connection id as another computer", () => {
  const reveal = vi.fn().mockResolvedValue(null);
  window.__octopRuntime = {
    version: 1,
    status: vi.fn(),
    start: vi.fn(),
    cancel: vi.fn(),
    subscribe: vi.fn(),
    reveal,
  };
  const file = { path: "/work/report.pdf", size: 10, sha256: "b".repeat(64) };
  render(
    <LocalFileResults
      messages={[
        {
          id: "old-receipt",
          role: "assistant",
          content: "",
          timestamp: 0,
          localArtifactDeviceId: "previous-login-connection",
          localArtifacts: [file],
        },
      ]}
    />,
  );
  expect(screen.getByText("本地任务成果")).toBeInTheDocument();
  expect(screen.queryByText(/成果保存在原电脑/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "打开文件位置" }));
  expect(reveal).toHaveBeenCalledWith({ path: file.path, sha256: file.sha256 });
});
