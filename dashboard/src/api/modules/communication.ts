import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";
import { newClientMessageId } from "../../utils/communicationId";
import { request, requestBlob } from "../request";
import type {
  CommunicationUser,
  CommunicationMessage,
  JobTitle,
  ConversationPage,
  Department,
  GroupInfo,
  GroupKind,
  GroupMember,
  ImageUpload,
  ImageUploadStatus,
  ManagedGroup,
  MessagePage,
  SendMessage,
  SendReceipt,
  Snapshot,
  SnapshotChunk,
  SyncPage,
} from "../types/communication";

export class CommunicationError extends Error {
  code: string;
  status: number;
  retryable: boolean;
  constructor(
    code: string,
    status: number,
    retryable: boolean,
    message: string,
  ) {
    super(message);
    this.name = "CommunicationError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}
export function communicationError(error: unknown): CommunicationError {
  if (error instanceof CommunicationError) return error;
  const text = error instanceof Error ? error.message : String(error);
  const status = Number(
    /Request failed:\s*(\d+)/.exec(text)?.[1] ??
      (text === "Unauthorized" ? "401" : "0"),
  );
  try {
    const json = JSON.parse(text.slice(text.indexOf("{"))) as {
      error?: { code?: string; message?: string; retryable?: boolean };
    };
    if (json.error)
      return new CommunicationError(
        json.error.code ?? "unavailable",
        status,
        json.error.retryable ?? status >= 500,
        json.error.message ?? text,
      );
  } catch {
    /* network/auth errors may not carry an envelope */
  }
  return new CommunicationError(
    "unavailable",
    status,
    status === 0 || status >= 500,
    text,
  );
}
async function call<T>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  try {
    const value = await request<{ data: T }>(`/im${path}`, {
      method,
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal,
    });
    if (!value || !("data" in value))
      throw new Error("Invalid communication response");
    return value.data;
  } catch (error) {
    if (signal?.aborted) throw error;
    throw communicationError(error);
  }
}
const id = (value: string) => encodeURIComponent(value);
const query = (values: Record<string, string | undefined>) =>
  new URLSearchParams(
    Object.entries(values).filter(
      (entry): entry is [string, string] => entry[1] !== undefined,
    ),
  ).toString();
export const communicationApi = {
  directorySettings: (signal?: AbortSignal) =>
    call<{ name: string }>("/directory-settings", "GET", undefined, signal),
  contacts: (signal?: AbortSignal) =>
    call<{ items: CommunicationUser[] }>("/contacts", "GET", undefined, signal),
  presence: (signal?: AbortSignal) =>
    call<{ online_user_ids: string[] }>("/presence", "GET", undefined, signal),
  departments: (signal?: AbortSignal) =>
    call<{ items: Department[] }>("/departments", "GET", undefined, signal),
  conversations: (cursor?: string, signal?: AbortSignal) =>
    call<ConversationPage>(
      `/conversations?${query({ limit: "100", include_hidden: "1", cursor })}`,
      "GET",
      undefined,
      signal,
    ),
  direct: (peer_user_id: string, signal?: AbortSignal) =>
    call<{ conversation_id: string }>(
      "/conversations/direct",
      "POST",
      { peer_user_id },
      signal,
    ),
  history: (conv: string, before?: string, signal?: AbortSignal) =>
    call<MessagePage>(
      `/conversations/${id(conv)}/messages?${query({
        limit: "50",
        before_seq: before,
      })}`,
      "GET",
      undefined,
      signal,
    ),
  send: (conv: string, body: SendMessage, signal?: AbortSignal) =>
    call<SendReceipt>(
      `/conversations/${id(conv)}/messages`,
      "POST",
      body,
      signal,
    ),
  message: (conv: string, message: string, signal?: AbortSignal) =>
    call<CommunicationMessage>(
      `/conversations/${id(conv)}/messages/${id(message)}`,
      "GET",
      undefined,
      signal,
    ),
  deleteConversation: (conv: string, signal?: AbortSignal) =>
    call(`/conversations/${id(conv)}`, "DELETE", undefined, signal),
  read: (conv: string, last_read_seq: string, signal?: AbortSignal) =>
    call<{ last_read_seq: string }>(
      `/conversations/${id(conv)}/read`,
      "POST",
      { last_read_seq },
      signal,
    ),
  state: (
    conv: string,
    body: { pinned?: boolean; muted?: boolean },
    signal?: AbortSignal,
  ) => call(`/conversations/${id(conv)}/state`, "PATCH", body, signal),
  hide: (conv: string, signal?: AbortSignal) =>
    call(`/conversations/${id(conv)}/hide`, "POST", undefined, signal),
  clear: (conv: string, signal?: AbortSignal) =>
    call(`/conversations/${id(conv)}/clear`, "POST", {}, signal),
  recall: (message: string, signal?: AbortSignal) =>
    call(`/messages/${id(message)}/recall`, "POST", undefined, signal),
  forward: (
    message: string,
    conversations: string[],
    signal?: AbortSignal,
    clientMessageId = newClientMessageId(),
  ) =>
    call<{ failed?: { conversation_id: string; error: string }[] }>(
      `/messages/${id(message)}/forward`,
      "POST",
      {
        client_message_id: clientMessageId,
        to_conversation_ids: conversations,
      },
      signal,
    ),
  groups: (kind?: GroupKind, signal?: AbortSignal) =>
    call<{ items: GroupInfo[] }>(
      `/groups?${query({ kind })}`,
      "GET",
      undefined,
      signal,
    ),
  createProjectGroup: (
    body: { request_id: string; name: string; user_ids: string[] },
    signal?: AbortSignal,
  ) => call<{ conversation_id: string }>("/groups", "POST", body, signal),
  createCompanyGroup: (
    body: { request_id: string; name?: string },
    signal?: AbortSignal,
  ) =>
    call<{ conversation_id: string }>(
      "/organization/company-group",
      "POST",
      body,
      signal,
    ),
  managedGroups: (signal?: AbortSignal) =>
    call<{ items: GroupInfo[] }>(
      "/organization/groups",
      "GET",
      undefined,
      signal,
    ),
  updateGroup: (
    conv: string,
    body: {
      request_id: string;
      name?: string;
      announcement?: string;
      mute_all?: boolean;
      members_can_invite?: boolean;
    },
    managed = false,
  ) =>
    call(`${managed ? "/organization" : ""}/groups/${id(conv)}`, "PATCH", body),
  groupMembers: (
    conv: string,
    body: { request_id: string; user_ids: string[] },
    remove = false,
    managed = false,
  ) =>
    call(
      `${managed ? "/organization" : ""}/groups/${id(conv)}/members`,
      remove ? "DELETE" : "POST",
      body,
    ),
  groupMember: (
    conv: string,
    user: string,
    body: {
      request_id: string;
      role?: "admin" | "member";
      mute_until?: string | null;
    },
    managed = false,
  ) =>
    call(
      `${managed ? "/organization" : ""}/groups/${id(conv)}/members/${id(
        user,
      )}`,
      "PATCH",
      body,
    ),
  groupOwner: (
    conv: string,
    body: { request_id: string; user_id: string },
    managed = false,
  ) =>
    call(
      `${managed ? "/organization" : ""}/groups/${id(conv)}/owner`,
      "PUT",
      body,
    ),
  leaveGroup: (conv: string, request_id: string) =>
    call(`/groups/${id(conv)}/leave`, "POST", { request_id }),
  archiveGroup: (conv: string, request_id: string, managed = false) =>
    call(
      `${managed ? "/organization" : ""}/groups/${id(conv)}/archive`,
      "POST",
      { request_id },
    ),
  group: (conv: string, signal?: AbortSignal) =>
    call<GroupInfo>(`/groups/${id(conv)}`, "GET", undefined, signal),
  members: (conv: string, signal?: AbortSignal) =>
    call<{ items: GroupMember[] }>(
      `/groups/${id(conv)}/members`,
      "GET",
      undefined,
      signal,
    ),
  nickname: (conv: string, my_nickname: string, signal?: AbortSignal) =>
    call(`/groups/${id(conv)}/my-membership`, "PATCH", { my_nickname }, signal),
  snapshot: (signal?: AbortSignal) =>
    call<Snapshot>("/sync/snapshot", "POST", undefined, signal),
  chunk: (snapshot: string, chunk: number, signal?: AbortSignal) =>
    call<SnapshotChunk>(
      `/sync/snapshots/${id(snapshot)}?chunk=${chunk}`,
      "GET",
      undefined,
      signal,
    ),
  sync: (cursor: string, signal?: AbortSignal) =>
    call<SyncPage>(
      `/sync?${query({ cursor, limit: "200" })}`,
      "GET",
      undefined,
      signal,
    ),
  ticket: (signal?: AbortSignal) =>
    request<{ ticket: string; expires_at: number }>("/im/realtime-ticket", {
      method: "POST",
      signal,
    }).catch((error) => {
      throw communicationError(error);
    }),
  media: (asset: string, signal?: AbortSignal) =>
    requestBlob(`/im/media/${id(asset)}?kind=original`, { signal }),
  uploadInit: (
    body: {
      purpose: "chat_image" | "chat_file";
      filename?: string;
      mime: string;
      size_bytes: number;
      sha256: string;
    },
    signal?: AbortSignal,
  ) => call<ImageUpload>("/media/uploads", "POST", body, signal),
  uploadBytes: (upload: string, file: File, signal?: AbortSignal) =>
    request(`/im/media/uploads/${id(upload)}/content`, {
      method: "PUT",
      headers: { "Content-Type": file.type || "application/octet-stream" },
      body: file,
      signal,
    }),
  uploadComplete: (upload: string, signal?: AbortSignal) =>
    call(`/media/uploads/${id(upload)}/complete`, "POST", undefined, signal),
  uploadStatus: (upload: string, signal?: AbortSignal) =>
    call<ImageUploadStatus>(
      `/media/uploads/${id(upload)}`,
      "GET",
      undefined,
      signal,
    ),
  uploadAbort: (upload: string) =>
    call(`/media/uploads/${id(upload)}`, "DELETE"),
  organization: {
    jobTitles: (signal?: AbortSignal) =>
      call<{ items: JobTitle[] }>(
        "/organization/job-titles",
        "GET",
        undefined,
        signal,
      ),
    createJobTitle: (name: string, signal?: AbortSignal) =>
      call<JobTitle>("/organization/job-titles", "POST", { name }, signal),
    assignJobTitle: (
      user: string,
      job_title_id: string | null,
      signal?: AbortSignal,
    ) =>
      call(
        `/organization/users/${id(user)}/job-title`,
        "PUT",
        { job_title_id },
        signal,
      ),
    directorySettings: (signal?: AbortSignal) =>
      call<{ name: string }>(
        "/organization/directory-settings",
        "GET",
        undefined,
        signal,
      ),
    saveDirectorySettings: (name: string, signal?: AbortSignal) =>
      call<{ name: string }>(
        "/organization/directory-settings",
        "PUT",
        { name },
        signal,
      ),
    departments: (signal?: AbortSignal) =>
      call<{ items: Department[] }>(
        "/organization/departments",
        "GET",
        undefined,
        signal,
      ),
    users: (signal?: AbortSignal) =>
      call<{ items: CommunicationUser[] }>(
        "/organization/users",
        "GET",
        undefined,
        signal,
      ),
    create: (body: Partial<Department>, signal?: AbortSignal) =>
      call<{ department_id: string }>(
        "/organization/departments",
        "POST",
        body,
        signal,
      ),
    update: (dept: string, body: Partial<Department>, signal?: AbortSignal) =>
      call(`/organization/departments/${id(dept)}`, "PATCH", body, signal),
    assign: (
      user: string,
      department_id: string | null,
      signal?: AbortSignal,
    ) =>
      call(
        `/organization/users/${id(user)}/department`,
        "PUT",
        { department_id },
        signal,
      ),
    createGroup: (dept: string, signal?: AbortSignal) =>
      call<{ conversation_id: string }>(
        `/organization/departments/${id(dept)}/group`,
        "POST",
        undefined,
        signal,
      ),
    group: (conv: string, signal?: AbortSignal) =>
      call<ManagedGroup>(
        `/organization/groups/${id(conv)}`,
        "GET",
        undefined,
        signal,
      ),
    updateGroup: (
      conv: string,
      body: { name?: string; announcement?: string; mute_all?: boolean },
      signal?: AbortSignal,
    ) => call(`/organization/groups/${id(conv)}`, "PATCH", body, signal),
    member: (
      conv: string,
      user: string,
      body: { role?: "admin" | "member"; mute_until?: string | null },
      signal?: AbortSignal,
    ) =>
      call(
        `/organization/groups/${id(conv)}/members/${id(user)}`,
        "PATCH",
        body,
        signal,
      ),
    owner: (conv: string, user_id: string, signal?: AbortSignal) =>
      call(
        `/organization/groups/${id(conv)}/owner`,
        "PUT",
        { user_id },
        signal,
      ),
  },
};
export function uploadCommunicationImage(file: File, signal: AbortSignal) {
  return uploadCommunicationAttachment(file, signal, "chat_image");
}
export function uploadCommunicationFile(file: File, signal: AbortSignal) {
  return uploadCommunicationAttachment(file, signal, "chat_file");
}
async function uploadCommunicationAttachment(
  file: File,
  signal: AbortSignal,
  purpose: "chat_image" | "chat_file",
): Promise<string> {
  const image = purpose === "chat_image";
  if (
    (image &&
      !["image/png", "image/jpeg", "image/gif", "image/webp"].includes(
        file.type,
      )) ||
    file.size > (image ? 10 : 50) * 1024 * 1024 ||
    file.size === 0
  ) {
    const code = image ? "invalid_image" : "invalid_file";
    throw new CommunicationError(code, 413, false, code);
  }
  const digest = sha256(new Uint8Array(await file.arrayBuffer()));
  signal.throwIfAborted();
  const hash = bytesToHex(digest);
  const upload = await communicationApi.uploadInit(
    {
      purpose,
      mime: file.type || "application/octet-stream",
      ...(image ? {} : { filename: file.name }),
      size_bytes: file.size,
      sha256: hash,
    },
    signal,
  );
  try {
    await communicationApi.uploadBytes(upload.upload_id, file, signal);
    await communicationApi.uploadComplete(upload.upload_id, signal);
  } catch (error) {
    void communicationApi.uploadAbort(upload.upload_id).catch(() => undefined);
    throw error;
  }
  return waitForCommunicationUpload(upload, signal, image);
}

export async function waitForCommunicationUpload(
  upload: ImageUpload,
  signal: AbortSignal,
  image = false,
): Promise<string> {
  try {
    for (let attempt = 0; attempt < 120; attempt++) {
      const status = await communicationApi.uploadStatus(
        upload.upload_id,
        signal,
      );
      if (status.state === "ready") return upload.asset_id;
      if (["failed", "aborted"].includes(status.state))
        throw new CommunicationError(
          image ? "image_failed" : "file_failed",
          422,
          false,
          status.last_error,
        );
      await new Promise<void>((resolve, reject) => {
        const abort = () => {
          clearTimeout(timer);
          reject(new DOMException("Aborted", "AbortError"));
        };
        const timer = setTimeout(() => {
          signal.removeEventListener("abort", abort);
          resolve();
        }, 500);
        signal.addEventListener("abort", abort, { once: true });
        if (signal.aborted) abort();
      });
    }
    throw new CommunicationError(
      image ? "image_timeout" : "file_timeout",
      504,
      true,
      "upload_timeout",
    );
  } catch (error) {
    void communicationApi.uploadAbort(upload.upload_id).catch(() => undefined);
    throw error;
  }
}
