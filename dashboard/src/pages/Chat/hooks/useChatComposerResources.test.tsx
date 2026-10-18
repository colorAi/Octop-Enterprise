/**
 * useChatComposerResources.test.tsx — per-expert knowledge base selection.
 *
 * Regression for the "remember KB selection" bug: one global selection array
 * + a touched flag reset on expert switch meant Expert B's selection leaked
 * into (or replaced) Expert A's remembered selection when switching back.
 * Selection is now persisted per expert (chatStorage) and restored on switch.
 */

import { renderHook, waitFor, act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api/modules/connectors", () => ({
  connectorsApi: {
    listInstances: vi.fn().mockResolvedValue([
      {
        status: "active",
        has_credentials: true,
        mcp_server_name: "c1",
        display_name: "C1",
        owner_user_id: 7,
        kind: "mcp",
      },
      {
        status: "active",
        has_credentials: true,
        mcp_server_name: "c2",
        display_name: "C2",
        owner_user_id: 7,
        kind: "mcp",
      },
    ]),
  },
}));
vi.mock("../../../api/modules/provider", () => ({
  providerApi: {
    listResolvedModels: vi.fn().mockResolvedValue([]),
    getActiveModel: vi.fn().mockResolvedValue(null),
  },
}));
vi.mock("../../../api/modules/preferences", () => ({
  preferencesApi: {
    get: vi.fn().mockResolvedValue(null),
    set: vi.fn().mockResolvedValue(undefined),
  },
}));
vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { patch: vi.fn().mockResolvedValue({}) },
}));
vi.mock("../../../api/request", () => ({
  request: vi.fn().mockResolvedValue(null),
}));
vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: {
    getCapability: vi.fn().mockResolvedValue({ usable: true }),
    list: vi.fn().mockResolvedValue([
      { id: "k1", default_open: false, owner_user_id: 7 },
      { id: "k2", default_open: false, owner_user_id: 7 },
      { id: "k3", default_open: false, owner_user_id: 7 },
      { id: "k4", default_open: false, owner_user_id: 7 },
    ]),
  },
}));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ id: 7 }),
}));
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [
      { agent_id: "expertA", knowledge_base_ids: ["k1"] },
      { agent_id: "expertB", knowledge_base_ids: ["k3"] },
    ],
  }),
}));

import { useChatComposerResources } from "./useChatComposerResources";
import { preferencesApi } from "../../../api/modules/preferences";
import { providerApi } from "../../../api/modules/provider";

beforeEach(() => {
  localStorage.clear();
});

describe("useChatComposerResources — per-expert KB selection", () => {
  it("restores expert A's manual selection after switching A -> B -> A", async () => {
    const threadId = "thread-existing"; // existing session → saved prefs apply
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string }) =>
        useChatComposerResources(agentId, threadId),
      { initialProps: { agentId: "expertA" } },
    );

    // A starts with its expert default (k1)
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1"]),
    );

    // user manually adds k2 in expert A
    act(() => result.current.handleKnowledgeBaseIdsChange(["k1", "k2"]));
    expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1", "k2"]);

    // switch to expert B: B's own default, nothing leaked from A
    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k3"]),
    );

    // user selects k3+k4 in expert B
    act(() => result.current.handleKnowledgeBaseIdsChange(["k3", "k4"]));

    // switch back to expert A: remembered k1+k2 (was: k3/k4 leak or reset)
    rerender({ agentId: "expertA" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1", "k2"]),
    );

    // and back to B: remembered k3+k4
    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k3", "k4"]),
    );
  });

  it("does not leak a cleared selection across experts", async () => {
    const threadId = "thread-existing";
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string }) =>
        useChatComposerResources(agentId, threadId),
      { initialProps: { agentId: "expertA" } },
    );
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k1"]),
    );

    // user clears A's selection entirely (explicit empty is a choice)
    act(() => result.current.handleKnowledgeBaseIdsChange([]));

    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual(["k3"]),
    );
    act(() => result.current.handleKnowledgeBaseIdsChange(["k4"]));

    rerender({ agentId: "expertA" });
    await waitFor(() =>
      expect(result.current.selectedKnowledgeBaseIds).toEqual([]),
    );
  });

  it("connectors get the same per-expert isolation (aligned with KB fix)", async () => {
    const threadId = "thread-existing";
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string }) =>
        useChatComposerResources(agentId, threadId),
      { initialProps: { agentId: "expertA" } },
    );
    await waitFor(() => expect(result.current.chatConnectors.length).toBe(2));

    // manual connector selection in A (saved per agent)
    act(() => result.current.handleConnectorsChange(["c1"]));
    expect(result.current.selectedConnectors).toEqual(["c1"]);

    // switch to B: A's c1 must NOT leak; B has no saved prefs / defaults
    rerender({ agentId: "expertB" });
    await waitFor(() => expect(result.current.selectedConnectors).toEqual([]));

    // manual selection in B, then back to A: each expert remembers its own
    act(() => result.current.handleConnectorsChange(["c2"]));
    rerender({ agentId: "expertA" });
    await waitFor(() =>
      expect(result.current.selectedConnectors).toEqual(["c1"]),
    );
    rerender({ agentId: "expertB" });
    await waitFor(() =>
      expect(result.current.selectedConnectors).toEqual(["c2"]),
    );
  });
});

describe("useChatComposerResources — new chat model selection", () => {
  const preferences = {
    locale: "zh",
    remote_browser_bookmarks: [],
    preferred_model: "company-local/flashnext",
    model_reasoning: {},
  };

  beforeEach(() => {
    vi.mocked(preferencesApi.get).mockResolvedValue(preferences);
    vi.mocked(providerApi.getActiveModel).mockResolvedValue({
      provider_name: "company-local",
      model: "flashnext",
    });
  });

  it("starts on Auto even when the user prefers the local model", async () => {
    const { result } = renderHook(() =>
      useChatComposerResources("expertA", null),
    );
    await waitFor(() =>
      expect(result.current.activeModelRef).toBe("company-local/flashnext"),
    );
    expect(result.current.selectedModel).toBeNull();
  });

  it("keeps a manual choice when preferences finish loading", async () => {
    let resolvePreferences!: (value: typeof preferences) => void;
    vi.mocked(preferencesApi.get).mockReturnValueOnce(
      new Promise((resolve) => {
        resolvePreferences = resolve;
      }),
    );
    const { result } = renderHook(() =>
      useChatComposerResources("expertA", null),
    );
    act(() => result.current.setSelectedModel("other/manual-model"));
    await act(async () => resolvePreferences(preferences));
    expect(result.current.selectedModel).toBe("other/manual-model");
  });

  it("restores an existing chat's choice and resets a new chat to Auto", async () => {
    const { result, rerender } = renderHook(
      ({ threadId, model }: { threadId: string | null; model?: string }) =>
        useChatComposerResources("expertA", threadId, model),
      {
        initialProps: {
          threadId: "existing-thread" as string | null,
          model: "other/saved-model" as string | undefined,
        },
      },
    );
    await waitFor(() => expect(result.current.modelsReady).toBe(true));
    expect(result.current.selectedModel).toBe("other/saved-model");
    await act(async () => {
      rerender({ threadId: null, model: undefined });
    });
    expect(result.current.selectedModel).toBeNull();
  });
});
