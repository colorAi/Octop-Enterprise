import type { OctopAgent } from "../../context/AgentContext";
import {
  isCompanyDesktop,
  requireDesktopRuntime,
} from "../../api/desktopRuntime";
import { getAuthToken, requestBlob } from "../../api/request";
import {
  CommunicationError,
  communicationApi,
  uploadCommunicationFile,
  uploadCommunicationImage,
  waitForCommunicationUpload,
} from "../../api/modules/communication";
import { uploadFile } from "../../api/modules/upload";
import { octopThreadsApi } from "../../api/modules/octopThreads";
import type { ChatAttachment } from "../Chat/hooks/useChat";
import type { SendMessage } from "../../api/types/communication";
import { newClientMessageId } from "../../utils/communicationId";
import {
  agentAttachmentAccessUrl,
  needsAuthBlobFetch,
  workspacePathFromAccessUrl,
} from "../../utils/toolMediaBlocks";
import { inferKindFromNameAndMime } from "../Chat/utils/chatAttachments";
import { authSessionIdentity } from "./hooks/communicationClient";
import {
  forwardSourceContent,
  hasForwardContent,
  isWholeForwardSelection,
  selectForwardContent,
  type ForwardContent,
  type ForwardDraft,
  type ForwardSource,
} from "./forwardContent";

const imageTypes = new Set([
  "image/png",
  "image/jpeg",
  "image/gif",
  "image/webp",
]);
export interface ForwardTarget {
  value: string;
  label: string;
  kind: "contact" | "conversation" | "agent";
  id: string;
  agent?: OctopAgent;
}
export interface ForwardResult {
  target: ForwardTarget;
  path?: string;
}

function splitText(text: string, limit: number): string[] {
  const chars = Array.from(text);
  const parts: string[] = [];
  for (let i = 0; i < chars.length; i += limit)
    parts.push(chars.slice(i, i + limit).join(""));
  return parts;
}
export function forwardMessageParts(
  text: string,
  assets: string[],
): SendMessage[] {
  const parts: Omit<SendMessage, "client_message_id">[] = [];
  if (text && (!assets.length || Array.from(text).length > 2000))
    parts.push(...splitText(text, 8000).map((text) => ({ text })));
  for (let i = 0; i < assets.length; i += 9)
    parts.push({
      asset_ids: assets.slice(i, i + 9),
      ...(i === 0 && Array.from(text).length <= 2000 ? { text } : {}),
    });
  return parts.map((part) => ({
    ...part,
    client_message_id: newClientMessageId(),
  }));
}

/** Fetch API files with the current account; never send its token to an external origin. */
async function attachmentFile(
  attachment: ChatAttachment,
  signal: AbortSignal,
): Promise<File> {
  const url = new URL(attachment.url, window.location.origin);
  let blob: Blob;
  if (
    url.origin === window.location.origin &&
    needsAuthBlobFetch(url.pathname)
  ) {
    blob = await requestBlob(url.pathname.replace(/^\/api/, "") + url.search, {
      signal,
    });
  } else {
    if (!["https:", "http:", "data:", "blob:"].includes(url.protocol))
      throw new CommunicationError("invalid_file", 422, false, "invalid_file");
    const response = await fetch(attachment.url, {
      signal,
      credentials: "omit",
      redirect: "error",
    });
    if (!response.ok)
      throw new CommunicationError(
        "file_failed",
        response.status,
        true,
        "file_failed",
      );
    blob = await response.blob();
  }
  let name =
    attachment.filename ||
    (
      attachment.workspacePath ||
      workspacePathFromAccessUrl(attachment.url) ||
      (["data:", "blob:"].includes(url.protocol) ? "attachment" : url.pathname)
    )
      .split(/[\\/]/)
      .pop() ||
    "attachment";
  const type =
    blob.type && blob.type !== "application/octet-stream"
      ? blob.type
      : attachment.mediaType;
  const extension = (
    {
      "image/png": ".png",
      "image/jpeg": ".jpg",
      "image/gif": ".gif",
      "image/webp": ".webp",
      "application/pdf": ".pdf",
    } as Record<string, string>
  )[type || ""];
  if (!/\.[A-Za-z0-9]{1,12}$/.test(name) && extension) name += extension;
  return new File([blob], name, { type: type || "application/octet-stream" });
}

/** One dialog owns one delivery: retries reuse receipts, uploads and per-part IDs. */
export class ForwardDelivery {
  private identity = authSessionIdentity();
  private content?: ForwardContent;
  private parts = new Map<string, SendMessage[]>();
  private assets: string[] = [];
  private progress = new Map<string, number>();
  private conversations = new Map<string, string>();
  private receipts = new Map<string, ForwardResult>();
  private forwardIds = new Map<string, string>();
  private agentAttachments = new Map<string, ChatAttachment[]>();
  private threads = new Map<string, string>();
  private source: ForwardSource;
  private signal: AbortSignal;
  private draft?: ForwardDraft;
  constructor(
    source: ForwardSource,
    signal: AbortSignal,
    draft?: ForwardDraft,
  ) {
    this.source = source;
    this.signal = signal;
    this.draft = draft && {
      note: draft.note.trim(),
      selection: {
        ...draft.selection,
        attachmentUrls: [...draft.selection.attachmentUrls],
        localArtifactPaths: [...draft.selection.localArtifactPaths],
      },
    };
  }
  private active() {
    this.signal.throwIfAborted();
    if (this.identity !== authSessionIdentity())
      throw new DOMException("Account changed", "AbortError");
  }
  private async loadContent() {
    this.active();
    let source = this.source;
    if (source.kind === "company") {
      const message = await communicationApi.message(
        source.conversationId,
        source.message.message_id,
        this.signal,
      );
      this.active();
      if (message.recalled || message.type === "system")
        throw new CommunicationError(
          "message_not_found",
          404,
          false,
          "message_not_found",
        );
      source = { ...source, message };
    }
    if (this.content) return this.content;
    const original = forwardSourceContent(source);
    const selected = this.draft
      ? selectForwardContent(original, this.draft.selection)
      : original;
    if (
      this.draft &&
      (selected.attachments.length !==
        this.draft.selection.attachmentUrls.length ||
        selected.localArtifacts.length !==
          this.draft.selection.localArtifactPaths.length)
    )
      throw new CommunicationError(
        "forward_content_changed",
        409,
        false,
        "forward_content_changed",
      );
    if (!hasForwardContent(selected))
      throw new CommunicationError(
        "invalid_request",
        422,
        false,
        "empty_forward",
      );
    return (this.content = {
      ...selected,
      text: [selected.text, this.draft?.note].filter(Boolean).join("\n\n"),
    });
  }
  private async companyParts(conversation: string) {
    const content = await this.loadContent();
    const existing = this.parts.get(conversation);
    if (existing) return existing;
    const total = content.attachments.length + content.localArtifacts.length;
    for (let i = this.assets.length; i < total; i++)
      await this.companyAsset(i, content);
    const parts = forwardMessageParts(content.text, this.assets);
    if (!parts.length)
      throw new CommunicationError(
        "invalid_request",
        422,
        false,
        "empty_forward",
      );
    this.parts.set(conversation, parts);
    return parts;
  }
  private async companyAsset(index: number, content: ForwardContent) {
    for (let i = this.assets.length; i <= index; i++) {
      this.active();
      let asset: string;
      if (i < content.attachments.length) {
        const file = await attachmentFile(content.attachments[i], this.signal);
        this.active();
        asset = await (imageTypes.has(file.type) &&
          file.size <= 10 * 1024 * 1024
          ? uploadCommunicationImage
          : uploadCommunicationFile)(file, this.signal);
      } else {
        const bridge = requireDesktopRuntime();
        if (!bridge.uploadArtifact)
          throw new CommunicationError(
            "local_transfer_unavailable",
            409,
            false,
            "local_transfer_unavailable",
          );
        const upload = await bridge.uploadArtifact({
          ...content.localArtifacts[i - content.attachments.length],
          token: getAuthToken(),
        });
        this.active();
        asset = await waitForCommunicationUpload(upload, this.signal);
      }
      this.active();
      this.assets.push(asset);
    }
    return this.assets[index];
  }
  async send(target: ForwardTarget): Promise<ForwardResult> {
    this.active();
    const existing = this.receipts.get(target.value);
    if (existing) return existing;
    if (target.kind === "agent") return this.toAgent(target);
    let conv = target.id;
    if (target.kind === "contact") {
      conv =
        this.conversations.get(target.id) ||
        (await communicationApi.direct(target.id, this.signal)).conversation_id;
      this.active();
      this.conversations.set(target.id, conv);
    }
    if (
      this.source.kind === "company" &&
      (!this.draft ||
        (!this.draft.note &&
          isWholeForwardSelection(
            forwardSourceContent(this.source),
            this.draft.selection,
          )))
    ) {
      const id = this.forwardIds.get(conv) || newClientMessageId();
      this.forwardIds.set(conv, id);
      const result = await communicationApi.forward(
        this.source.message.message_id,
        [conv],
        this.signal,
        id,
      );
      if (result.failed?.length)
        throw new CommunicationError(
          result.failed[0].error,
          403,
          false,
          result.failed[0].error,
        );
    } else {
      const parts = await this.companyParts(conv);
      for (let i = this.progress.get(conv) ?? 0; i < parts.length; i++) {
        this.active();
        await communicationApi.send(conv, parts[i], this.signal);
        this.active();
        this.progress.set(conv, i + 1);
      }
    }
    this.active();
    const result = { target, path: `/messages/${encodeURIComponent(conv)}` };
    this.receipts.set(target.value, result);
    return result;
  }
  private async toAgent(target: ForwardTarget): Promise<ForwardResult> {
    const agent = target.agent!;
    const content = await this.loadContent();
    const native = isCompanyDesktop();
    const total = content.attachments.length + content.localArtifacts.length;
    if (
      (!native && content.localArtifacts.length) ||
      (native && total && !requireDesktopRuntime().importAttachment)
    )
      throw new CommunicationError(
        "local_transfer_unavailable",
        409,
        false,
        "local_transfer_unavailable",
      );
    let thread = this.threads.get(target.id);
    if (!thread) {
      thread = (await octopThreadsApi.create(target.id)).thread_id;
      this.active();
      this.threads.set(target.id, thread);
    }
    let text = content.text;
    let attachments = this.agentAttachments.get(target.id);
    if (native) {
      const runtime = requireDesktopRuntime();
      attachments ??= [];
      this.agentAttachments.set(target.id, attachments);
      for (let i = attachments.length; i < total; i++) {
        this.active();
        let input: Parameters<NonNullable<typeof runtime.importAttachment>>[0];
        if (i < content.attachments.length) {
          const attachment = content.attachments[i];
          const url = new URL(attachment.url, window.location.origin);
          const match =
            url.origin === window.location.origin &&
            /^\/api\/im\/media\/([A-Za-z0-9_-]+)$/.exec(url.pathname);
          const asset = match ? match[1] : await this.companyAsset(i, content);
          input = {
            asset_id: asset,
            filename: attachment.filename,
            thread_id: thread,
            token: getAuthToken(),
          };
        } else {
          const file = content.localArtifacts[i - content.attachments.length];
          input = {
            path: file.path,
            sha256: file.sha256,
            thread_id: thread,
            token: getAuthToken(),
          };
        }
        this.active();
        const file = await runtime.importAttachment!(input);
        this.active();
        const source = content.attachments[i];
        const filename = source?.filename || file.path.split(/[\\/]/).pop();
        attachments.push({
          url: "",
          filename,
          mediaType: source?.mediaType,
          workspacePath: file.path,
          kind: inferKindFromNameAndMime(
            source?.mediaType,
            filename,
            source?.kind,
          ),
          localFile: file,
        });
      }
      const paths = attachments.map((attachment) => attachment.localFile!.path);
      if (paths.length)
        text = [text, ...paths.map((path) => `[file: ${path}]`)]
          .filter(Boolean)
          .join("\n\n");
    } else {
      attachments ??= [];
      this.agentAttachments.set(target.id, attachments);
      for (let i = attachments.length; i < content.attachments.length; i++) {
        const attachment = content.attachments[i];
        const file = await attachmentFile(attachment, this.signal);
        this.active();
        const stored = await uploadFile(target.id, file);
        this.active();
        attachments.push({
          url:
            stored.access_url ||
            stored.url ||
            agentAttachmentAccessUrl(target.id, stored.path, stored.media_type),
          workspacePath: stored.path || stored.workspace_path,
          filename: stored.filename,
          mediaType: stored.media_type,
          kind: inferKindFromNameAndMime(stored.media_type, stored.filename),
        });
      }
    }
    this.active();
    const store = await import("../Chat/hooks/chatStore");
    this.active();
    store.appendUserMessage(thread, {
      id: newClientMessageId(),
      role: "user",
      content: text,
      attachments,
      status: "done",
      timestamp: Date.now(),
    });
    void store.sendTurn(
      thread,
      text,
      target.id,
      "",
      attachments,
      () =>
        store.emitSessionEvent({
          kind: "sessionsChanged",
          sessionId: thread,
          agentId: target.id,
        }),
      agent.default_model,
      thread,
      agent.mcp_servers,
      agent.knowledge_base_ids,
    );
    store.emitSessionEvent({
      kind: "sessionsChanged",
      sessionId: thread,
      agentId: target.id,
    });
    const result = {
      target,
      path: `/chat/${encodeURIComponent(target.id)}/${encodeURIComponent(
        thread,
      )}`,
    };
    this.receipts.set(target.value, result);
    return result;
  }
}
