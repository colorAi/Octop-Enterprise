import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import {
  octopThreadsApi,
  type ContextUsageBreakdown,
} from "../../../api/modules/octopThreads";
import ContextWindowRing from "./ContextWindowRing";

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { contextUsage: vi.fn() },
}));
vi.mock("../hooks/useSessions", () => ({ isPendingThread: () => false }));
vi.mock("antd", () => ({
  Popover: ({
    children,
    content,
    open,
    onOpenChange,
  }: {
    children: ReactNode;
    content: ReactNode;
    open: boolean;
    onOpenChange: (open: boolean) => void;
  }) => (
    <>
      <div onClick={() => onOpenChange(!open)}>{children}</div>
      {open ? content : null}
    </>
  ),
  Spin: () => <span>Loading</span>,
  Drawer: ({ children, open }: { children: ReactNode; open: boolean }) =>
    open ? children : null,
}));

const detailed: ContextUsageBreakdown = {
  max_tokens: 128000,
  used_tokens: 12000,
  segments: [
    { key: "system_prompt", tokens: 2000 },
    { key: "tool_definitions", tokens: 5000 },
    { key: "conversation", tokens: 5000 },
  ],
};
const label = (segment: string) => `chat.contextWindow.segments.${segment}`;
afterEach(() => vi.clearAllMocks());

it("shows live tool composition without waiting for company history synchronization", () => {
  render(
    <ContextWindowRing
      agentId="expert"
      threadId="local"
      usedTokens={12000}
      maxTokens={128000}
      liveBreakdown={detailed}
    />,
  );
  fireEvent.click(screen.getByRole("progressbar"));
  expect(screen.getByText(label("tool_definitions"))).toBeInTheDocument();
  expect(screen.getByText(label("system_prompt"))).toBeInTheDocument();
  expect(octopThreadsApi.contextUsage).not.toHaveBeenCalled();
});

it("replaces an incomplete cached result immediately when live categories arrive", async () => {
  vi.mocked(octopThreadsApi.contextUsage).mockResolvedValue({
    max_tokens: 128000,
    used_tokens: 12000,
    segments: [],
  });
  const { rerender } = render(
    <ContextWindowRing
      agentId="expert"
      threadId="local"
      usedTokens={12000}
      maxTokens={128000}
    />,
  );
  fireEvent.click(screen.getByRole("progressbar"));
  expect(await screen.findByText(label("unclassified"))).toBeInTheDocument();
  expect(screen.queryByText(label("conversation"))).not.toBeInTheDocument();
  rerender(
    <ContextWindowRing
      agentId="expert"
      threadId="local"
      usedTokens={12000}
      maxTokens={128000}
      liveBreakdown={detailed}
    />,
  );
  expect(screen.getByText(label("tool_definitions"))).toBeInTheDocument();
  expect(screen.queryByText(label("unclassified"))).not.toBeInTheDocument();
});

it("does not attribute the whole input to conversation when categories are unavailable", async () => {
  vi.mocked(octopThreadsApi.contextUsage).mockRejectedValue(
    new Error("offline"),
  );
  render(
    <ContextWindowRing
      agentId="expert"
      threadId="local"
      usedTokens={12000}
      maxTokens={128000}
    />,
  );
  fireEvent.click(screen.getByRole("progressbar"));
  expect(await screen.findByText(label("unclassified"))).toBeInTheDocument();
  expect(screen.queryByText(label("conversation"))).not.toBeInTheDocument();
});

it("refreshes a cached breakdown when a newer model call changes the total", async () => {
  const request = vi.mocked(octopThreadsApi.contextUsage);
  request
    .mockResolvedValueOnce(detailed)
    .mockResolvedValueOnce({ ...detailed, used_tokens: 15000 });
  const { rerender } = render(
    <ContextWindowRing
      agentId="expert"
      threadId="local"
      usedTokens={12000}
      maxTokens={128000}
    />,
  );
  fireEvent.click(screen.getByRole("progressbar"));
  expect(
    await screen.findByText(label("tool_definitions")),
  ).toBeInTheDocument();
  rerender(
    <ContextWindowRing
      agentId="expert"
      threadId="local"
      usedTokens={15000}
      maxTokens={128000}
    />,
  );
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
  expect(
    await screen.findByText(label("tool_definitions")),
  ).toBeInTheDocument();
});

it("ignores an old chat's late response after switching to another thread", async () => {
  let finish!: (result: ContextUsageBreakdown) => void;
  vi.mocked(octopThreadsApi.contextUsage)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValueOnce({
      max_tokens: 128000,
      used_tokens: 6000,
      segments: [],
    });
  const { rerender } = render(
    <ContextWindowRing
      agentId="expert"
      threadId="old"
      usedTokens={12000}
      maxTokens={128000}
    />,
  );
  fireEvent.click(screen.getByRole("progressbar"));
  rerender(
    <ContextWindowRing
      agentId="expert"
      threadId="new"
      usedTokens={6000}
      maxTokens={128000}
    />,
  );
  expect(await screen.findByText(label("unclassified"))).toBeInTheDocument();
  await act(async () => finish(detailed));
  expect(screen.queryByText(label("tool_definitions"))).not.toBeInTheDocument();
});
