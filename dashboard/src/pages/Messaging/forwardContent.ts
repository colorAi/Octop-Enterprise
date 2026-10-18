import type { LocalArtifact } from "../../api/desktopRuntime";
import type { CommunicationMessage } from "../../api/types/communication";
import type { ChatAttachment, ChatMessage } from "../Chat/hooks/useChat";
import { collectChatFilePaths } from "../Chat/hooks/useChatFileDetection";
import { deriveMessageContent } from "../Chat/utils/messageContent";
import { stripLocalAttachmentHints } from "../Chat/utils/chatAttachments";
import {
  agentAttachmentAccessUrl,
  collectToolMediaFromToolData,
  dedupeToolMedia,
  resolveToolMediaUrl,
  workspacePathFromAccessUrl,
} from "../../utils/toolMediaBlocks";

export interface ForwardContent {
  text: string;
  attachments: ChatAttachment[];
  localArtifacts: LocalArtifact[];
}
export type ForwardSource =
  | { kind: "agent"; content: ForwardContent }
  | { kind: "company"; conversationId: string; message: CommunicationMessage };

export interface ForwardSelection {
  text: boolean;
  attachmentUrls: string[];
  localArtifactPaths: string[];
}
export interface ForwardDraft {
  selection: ForwardSelection;
  note: string;
}

export function forwardSourceContent(source: ForwardSource): ForwardContent {
  if (source.kind === "agent") return source.content;
  return {
    text: source.message.text || "",
    localArtifacts: [],
    attachments: (source.message.attachments ?? []).map((file) => ({
      url: `/api/im/media/${encodeURIComponent(file.asset_id)}?kind=original`,
      filename: file.filename,
      mediaType: file.mime,
      kind: file.purpose === "chat_image" ? "image" : "file",
    })),
  };
}

export function allForwardSelection(content: ForwardContent): ForwardSelection {
  return {
    text: Boolean(content.text.trim()),
    attachmentUrls: content.attachments.map((file) => file.url),
    localArtifactPaths: content.localArtifacts.map((file) => file.path),
  };
}

export function selectForwardContent(
  content: ForwardContent,
  selection: ForwardSelection,
): ForwardContent {
  return {
    text: selection.text ? content.text : "",
    attachments: content.attachments.filter((file) =>
      selection.attachmentUrls.includes(file.url),
    ),
    localArtifacts: content.localArtifacts.filter((file) =>
      selection.localArtifactPaths.includes(file.path),
    ),
  };
}

export function hasForwardContent(content: ForwardContent): boolean {
  return Boolean(
    content.text.trim() ||
      content.attachments.length ||
      content.localArtifacts.length,
  );
}

export function isWholeForwardSelection(
  content: ForwardContent,
  selection: ForwardSelection,
): boolean {
  return (
    (!content.text.trim() || selection.text) &&
    selection.attachmentUrls.length === content.attachments.length &&
    selection.localArtifactPaths.length === content.localArtifacts.length &&
    content.attachments.every((file) =>
      selection.attachmentUrls.includes(file.url),
    ) &&
    content.localArtifacts.every((file) =>
      selection.localArtifactPaths.includes(file.path),
    )
  );
}

/** Collect the visible answer and its deliverables, excluding reasoning/tool logs. */
export function agentForwardContent(
  messages: ChatMessage[],
  agentId?: string | null,
): ForwardContent {
  const local = new Map<string, LocalArtifact>();
  for (const message of messages) {
    for (const file of message.localArtifacts ?? []) local.set(file.path, file);
    for (const attachment of message.attachments ?? [])
      if (attachment.localFile)
        local.set(attachment.localFile.path, attachment.localFile);
  }
  const attachments = new Map<string, ChatAttachment>();
  const add = (attachment: ChatAttachment, owner?: string | null) => {
    const path =
      attachment.workspacePath || workspacePathFromAccessUrl(attachment.url);
    if (
      path &&
      [...local.keys()].some((file) => {
        const normalized = path
          .replaceAll("\\", "/")
          .replace(/^file:\/\//, "")
          .replace(/^\.\//, "");
        const full = file.replaceAll("\\", "/");
        return (
          full === normalized ||
          (!normalized.startsWith("/") && full.endsWith(`/${normalized}`))
        );
      })
    )
      return;
    const key = path ? `${owner}:${path.replace(/^\/+/, "")}` : attachment.url;
    if (!attachments.has(key)) attachments.set(key, attachment);
  };
  const texts: string[] = [];
  for (const message of messages) {
    if (message.status === "error" || message.hitlData) continue;
    const owner = message.speakerAgentId || agentId;
    for (const attachment of message.attachments ?? []) add(attachment, owner);
    const media = collectToolMediaFromToolData(
      message.toolData,
      owner,
      message.attachments,
    );
    for (const item of [...media.images, ...media.videos])
      add(
        {
          url: item.url,
          filename: item.filename,
          mediaType: item.mimeType,
          kind: item.kind,
        },
        owner,
      );
    for (const item of media.files) add({ ...item, kind: "file" }, owner);
    if (owner && !message.localArtifacts?.length)
      for (const path of collectChatFilePaths([message], owner))
        add(
          {
            url: agentAttachmentAccessUrl(owner, path),
            workspacePath: path,
            filename: path.split(/[\\/]/).pop(),
            kind: "file",
          },
          owner,
        );
    if (message.toolData || !["user", "assistant"].includes(message.role))
      continue;
    const { textContent } = deriveMessageContent(message);
    const text = stripLocalAttachmentHints(
      textContent,
      message.attachments ?? [],
    )
      .replace(
        /!?\[([^\]\n]*)\]\(([^)\n]+)\)/g,
        (match, label: string, raw: string) => {
          const url = raw.trim().replace(/^<|>$/g, "");
          const path = workspacePathFromAccessUrl(url);
          const file = [...local.values()].find(
            (item) => item.path === url || item.path === path,
          );
          if (file) return label || file.path.split(/[\\/]/).pop() || "";
          // Web links remain text; media and workspace links become accessible copies.
          const inlineImage = match.startsWith("!");
          const relativeFile =
            !url.includes(":") &&
            !url.startsWith("#") &&
            /\.[A-Za-z0-9]{1,12}(?:\?|$)/.test(url);
          if (
            !owner ||
            (!path &&
              !url.startsWith("file://") &&
              !url.startsWith("/api/") &&
              !inlineImage &&
              !relativeFile)
          )
            return match;
          const resolved = resolveToolMediaUrl(owner, url);
          if (!resolved) return match;
          add(
            {
              url: resolved,
              filename:
                path?.split(/[\\/]/).pop() ||
                (relativeFile ? url.split(/[\\/]/).pop() : undefined),
              kind: inlineImage ? "image" : "file",
            },
            owner,
          );
          return label;
        },
      )
      .trim();
    if (
      text &&
      !(message.localArtifacts?.length && text === "本次任务生成的本地文件")
    )
      texts.push(text);
  }
  return {
    text: texts.join("\n\n"),
    attachments: dedupeToolMedia([...attachments.values()]),
    localArtifacts: [...local.values()],
  };
}
