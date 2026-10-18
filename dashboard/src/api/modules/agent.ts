import { request } from "../request";
import type {
  AgentRequest,
  AgentsRunningConfig,
  HeartbeatConfig,
  ProactiveConfig,
  ProactiveCareConfig,
  TurnRequest,
} from "../types";

export interface AgentRunningStatus {
  is_running: boolean;
  active_count: number;
  user_id?: string | null;
  session_id?: string | null;
  channel?: string | null;
}

// Agent API
export const agentApi = {
  agentRoot: () => request<unknown>("/agent/"),

  healthCheck: () => request<unknown>("/agent/health"),

  getRunningStatus: (params?: {
    user_id?: string;
    session_id?: string;
    channel?: string;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.user_id) searchParams.set("user_id", params.user_id);
    if (params?.session_id) searchParams.set("session_id", params.session_id);
    if (params?.channel) searchParams.set("channel", params.channel);
    const qs = searchParams.toString();
    return request<AgentRunningStatus>(
      `/agent/running-status${qs ? `?${qs}` : ""}`,
    );
  },

  agentApi: (body: AgentRequest) =>
    request<unknown>("/agent/process", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  turnsStream: (body: TurnRequest) =>
    request<string>("/turns/stream", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  getProcessStatus: () => request<unknown>("/agent/admin/status"),

  shutdownSimple: () =>
    request<void>("/agent/shutdown", {
      method: "POST",
    }),

  shutdown: () =>
    request<void>("/agent/admin/shutdown", {
      method: "POST",
    }),

  getAgentRunningConfig: () =>
    request<AgentsRunningConfig>("/agent/running-config"),

  updateAgentRunningConfig: (config: AgentsRunningConfig) =>
    request<AgentsRunningConfig>("/agent/running-config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  getHeartbeatConfig: () => request<HeartbeatConfig>("/agent/heartbeat-config"),

  updateHeartbeatConfig: (config: HeartbeatConfig) =>
    request<HeartbeatConfig>("/agent/heartbeat-config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  getProactiveConfig: () => request<ProactiveConfig>("/agent/proactive-config"),

  updateProactiveConfig: (config: ProactiveConfig) =>
    request<ProactiveConfig>("/agent/proactive-config", {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  getProactiveCareConfig: (agentId: string) =>
    request<ProactiveCareConfig>(`/agents/${agentId}/proactive-care`),

  updateProactiveCareConfig: (agentId: string, config: ProactiveCareConfig) =>
    request<ProactiveCareConfig>(`/agents/${agentId}/proactive-care`, {
      method: "PUT",
      body: JSON.stringify(config),
    }),

  getBackgroundUsage: () =>
    request<BackgroundUsageResponse>("/agent/background-usage"),

  getUsageSummary: (params: {
    time_window?: string;
    granularity?: string;
    top_n?: number;
    start_date?: string;
    end_date?: string;
  }) =>
    request<{
      time_window: string;
      granularity: string;
      range_start: string | null;
      range_end: string | null;
      input_tokens: number;
      output_tokens: number;
      total_tokens: number;
      turns: number;
      avg_per_turn: number;
      buckets: Array<{
        key: string;
        label: string;
        input_tokens: number;
        output_tokens: number;
        total_tokens: number;
        turns: number;
      }>;
    }>("/agent/usage/summary", {
      method: "POST",
      body: JSON.stringify(params),
    }),
};

export interface BackgroundUsageBucket {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  calls: number;
}

export interface BackgroundUsageResponse {
  buckets: Record<string, BackgroundUsageBucket>;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_calls: number;
}
