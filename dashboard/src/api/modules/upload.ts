import { requestUpload } from "../request";

export interface UploadResponse {
  path: string;
  workspace_path: string;
  filename: string;
  media_type: string;
  url: string;
  access_url: string;
}

/**
 * Upload a chat attachment into the agent workspace ``inbound/`` directory.
 */
export async function uploadFile(
  agentId: string,
  file: File,
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return requestUpload<UploadResponse>(`/agents/${agentId}/upload`, formData);
}

export const uploadImage = uploadFile;

export const uploadApi = { uploadFile, uploadImage };
