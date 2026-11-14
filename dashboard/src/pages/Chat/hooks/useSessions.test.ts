import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as chatStore from "./chatStore";
import {
  resetSessionStoreForTests,
  sortSessions,
  toSession,
  useSessions,
  type Session,
} from "./useSessions";

const listMock = vi.fn();

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    list: (...args: unknown[]) => listMock(...args),
    create: vi.fn(),
    delete: vi.fn(),
    patch: vi.fn(),
    rename: vi.fn(),
    rebind: vi.fn(),
  },
  normalizeThreadArtifacts: () => [],
}));

function threadRow(threadId: string, agentExtra?: Partial<{ title: string }>) {
  return {
    thread_id: threadId,
    title: agentExtra?.title ?? null,
    last_active: 1,
    created_at: 1,
    channel_type: "dashboard",
    is_active: false,
    has_messages: true,
    pinned: false,
  };
}

describe("toSession / sortSessions ordering", () => {
  it("sorts empty new chats by created_at above older active ones", () => {
    const olderActive = toSession({
      thread_id: "thr_old",
      title: "old",
      last_active: 100,
      created_at: 10,
      has_messages: true,
    });
    const emptyNew = toSession({
      thread_id: "thr_new",
      title: null,
      last_active: 0,
      created_at: 200,
      has_messages: false,
    });
    expect(
      sortSessions([olderActive, emptyNew]).map((s: Session) => s.id),
    ).toEqual(["thr_new", "thr_old"]);
  });

  it("keeps pinned sessions first", () => {
    const pinned = toSession({
      thread_id: "thr_pin",
      title: "pin",
      last_active: 1,
      created_at: 1,
      pinned: true,
    });
    const recent = toSession({
      thread_id: "thr_recent",
      title: "recent",
      last_active: 999,
      created_at: 999,
    });
    expect(sortSessions([recent, pinned]).map((s) => s.id)).toEqual([
      "thr_pin",
      "thr_recent",
    ]);
  });

  it("maps turn_active and awaiting_user onto the session", () => {
    const session = toSession({
      thread_id: "thr_work",
      title: "busy",
      last_active: 1,
      turn_active: true,
      awaiting_user: true,
    });
    expect(session.turnActive).toBe(true);
    expect(session.awaitingUser).toBe(true);
  });
});

describe("useSessions agent switch", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    listMock.mockReset();
  });

  afterEach(() => {
    localStorage.removeItem("auth_token");
    resetSessionStoreForTests();
  });

  it("isolates drafts and pending prefills when another account opens the same expert", async () => {
    listMock.mockResolvedValue([]);
    localStorage.setItem("auth_token", "draft-login-a");
    const first = renderHook(() => useSessions("shared-expert"));
    await waitFor(() => expect(first.result.current.loading).toBe(false));
    chatStore.writeInputDraft("shared-expert", null, "private draft");
    chatStore.setPendingPrefillText("private prefill");
    first.unmount();
    localStorage.setItem("auth_token", "draft-login-b");
    // ChatPage consumes prefill before useSessions runs.
    expect(chatStore.consumePendingPrefillText()).toBe("");
    const second = renderHook(() => useSessions("shared-expert"));
    await waitFor(() => expect(second.result.current.loading).toBe(false));
    expect(chatStore.readInputDraft("shared-expert", null)).toBe("");
    chatStore.writeInputDraft("shared-expert", null, "B's draft");
    chatStore.writeInputDraft(
      "shared-expert",
      null,
      "late A draft",
      "draft-login-a",
    );
    expect(chatStore.readInputDraft("shared-expert", null)).toBe("B's draft");
  });

  it("clears old account history and drops late responses for the same expert", async () => {
    localStorage.setItem("auth_token", "login-a");
    listMock.mockResolvedValueOnce([threadRow("account-a")]);
    const { result, rerender } = renderHook(() => useSessions("shared-expert"));
    await waitFor(() =>
      expect(result.current.sessions[0]?.id).toBe("account-a"),
    );
    chatStore.setHistoryPage(
      "account-a",
      [{ id: "cached", role: "user", content: "private", timestamp: 1 }],
      { hasMore: false, nextOffset: 1 },
    );
    let resolveOld!: (rows: ReturnType<typeof threadRow>[]) => void;
    listMock.mockReturnValueOnce(
      new Promise((resolve) => (resolveOld = resolve)),
    );
    let pending!: ReturnType<typeof result.current.fetchSessions>;
    act(() => {
      pending = result.current.fetchSessions();
    });

    localStorage.setItem("auth_token", "login-b");
    listMock.mockResolvedValueOnce([threadRow("account-b")]);
    rerender();
    expect(result.current.sessions).toEqual([]);
    expect(chatStore.getSnapshot("account-a").messages).toEqual([]);
    await waitFor(() =>
      expect(result.current.sessions[0]?.id).toBe("account-b"),
    );

    await act(async () => {
      resolveOld([threadRow("account-a")]);
      expect(await pending).toEqual([]);
    });
    expect(result.current.sessions.map((session) => session.id)).toEqual([
      "account-b",
    ]);
  });

  it("clears previous-agent threads on the first render of a new agent", async () => {
    listMock.mockImplementation(async (agentId: string) => {
      if (agentId === "agent-a") {
        return [threadRow("thr_from_a")];
      }
      // Keep B's fetch pending so we can observe the sync clear.
      return new Promise(() => {});
    });

    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string | null }) => useSessions(agentId),
      { initialProps: { agentId: "agent-a" } },
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.sessions.map((s) => s.id)).toEqual(["thr_from_a"]);
    });

    rerender({ agentId: "agent-b" });

    // Critical: no one-frame leak of agent-a's thr_* into agent-b.
    expect(result.current.sessions).toEqual([]);
    expect(result.current.loading).toBe(true);
    expect(result.current.sessions.some((s) => s.id === "thr_from_a")).toBe(
      false,
    );
  });

  it("ensureThreadInList returns unknown on probe network errors", async () => {
    listMock
      .mockResolvedValueOnce([]) // initial fetch for agent
      .mockRejectedValueOnce(new Error("network down")); // probe

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_foreign");
    });
    expect(probe).toBe("unknown");
  });

  it("ensureThreadInList returns missing when probe confirms absence", async () => {
    listMock.mockResolvedValue([]);

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_foreign");
    });
    expect(probe).toBe("missing");
  });

  it("ensureThreadInList returns found when the thread is already listed", async () => {
    listMock.mockResolvedValue([threadRow("thr_ok")]);

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.sessions.map((s) => s.id)).toEqual(["thr_ok"]);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_ok");
    });
    expect(probe).toBe("found");
    expect(listMock).toHaveBeenCalledTimes(1);
  });
});
