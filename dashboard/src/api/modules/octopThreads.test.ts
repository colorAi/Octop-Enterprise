import { afterEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "../desktopRuntime";
import { request } from "../request";
import { octopThreadsApi } from "./octopThreads";

vi.mock("../request", () => ({ request: vi.fn().mockResolvedValue({}) }));

afterEach(() => {
  delete window.__octopRuntime;
  vi.clearAllMocks();
});

it("scopes browser lists and direct conversation actions to web history", async () => {
  await octopThreadsApi.list("expert", 11);
  await octopThreadsApi.history("expert", "old-link");
  await octopThreadsApi.patch("expert", "old-link", { title: "Title" });
  await octopThreadsApi.delete("expert", "old-link");
  await octopThreadsApi.rebind("expert", "old-link");
  await octopThreadsApi.continue("expert", "old-link", "other");
  expect(vi.mocked(request).mock.calls).toHaveLength(6);
  for (const [path] of vi.mocked(request).mock.calls) {
    const url = new URL(path, "https://company.test");
    expect(url.searchParams.get("surface")).toBe("web");
  }
  expect(
    new URL(
      vi.mocked(request).mock.calls[0][0],
      "https://company.test",
    ).searchParams.get("limit"),
  ).toBe("11");
});

it("preserves existing desktop history requests", async () => {
  window.__octopRuntime = { version: 1 } as DesktopRuntimeBridge;
  await octopThreadsApi.list("expert", 11);
  await octopThreadsApi.history("expert", "local-thread");
  expect(request).toHaveBeenNthCalledWith(1, "/agents/expert/threads?limit=11");
  expect(request).toHaveBeenNthCalledWith(
    2,
    "/agents/expert/threads/local-thread/history?limit=25&offset=0",
  );
});
