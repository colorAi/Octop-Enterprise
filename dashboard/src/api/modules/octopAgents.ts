import { request, requestUpload } from "../request";

export const octopAgentsApi = {
  privateInstance: (agentId: string, signal?: AbortSignal) =>
    request<{ agent_id: string }>(
      `/agents/${encodeURIComponent(agentId)}/private-instance`,
      { method: "POST", signal },
    ),
  markRead: (agentId: string) =>
    request<void>(`/agents/${encodeURIComponent(agentId)}/read`, {
      method: "POST",
    }),

  uploadAvatar: (agentId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return requestUpload<{ icon_url: string }>(
      `/agents/${encodeURIComponent(agentId)}/avatar`,
      body,
    );
  },

  deleteAvatar: (agentId: string) =>
    request<void>(`/agents/${encodeURIComponent(agentId)}/avatar`, {
      method: "DELETE",
    }),
};
