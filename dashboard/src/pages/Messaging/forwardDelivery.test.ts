import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "../../api/desktopRuntime";
import type { OctopAgent } from "../../context/AgentContext";
import {
  ForwardDelivery,
  forwardMessageParts,
  type ForwardTarget,
} from "./forwardDelivery";
import {
  allForwardSelection,
  forwardSourceContent,
  type ForwardDraft,
  type ForwardSource,
} from "./forwardContent";

const mock = vi.hoisted(() => ({
  identity: "account:session",
  api: { send: vi.fn(), direct: vi.fn(), message: vi.fn(), forward: vi.fn() },
  image: vi.fn(),
  file: vi.fn(),
  wait: vi.fn(),
  blob: vi.fn(),
  upload: vi.fn(),
  create: vi.fn(),
  append: vi.fn(),
  turn: vi.fn(),
  event: vi.fn(),
}));
vi.mock("../../api/modules/communication", async (original) => ({
  ...(await original<typeof import("../../api/modules/communication")>()),
  communicationApi: mock.api,
  uploadCommunicationImage: mock.image,
  uploadCommunicationFile: mock.file,
  waitForCommunicationUpload: mock.wait,
}));
vi.mock("../../api/request", () => ({
  getAuthToken: () => "current-token",
  requestBlob: mock.blob,
}));
vi.mock("./hooks/communicationClient", () => ({
  authSessionIdentity: () => mock.identity,
}));
vi.mock("../../api/modules/upload", () => ({ uploadFile: mock.upload }));
vi.mock("../../api/modules/octopThreads", () => ({
  octopThreadsApi: { create: mock.create },
}));
vi.mock("../Chat/hooks/chatStore", () => ({
  appendUserMessage: mock.append,
  sendTurn: mock.turn,
  emitSessionEvent: mock.event,
}));

const group: ForwardTarget = {
  value: "conversation:group",
  label: "Group",
  kind: "conversation",
  id: "group",
};
const agent: ForwardTarget = {
  value: "agent:agent",
  label: "Agent",
  kind: "agent",
  id: "agent",
  agent: {
    agent_id: "agent",
    name: "Agent",
    default_model: "model",
  } as OctopAgent,
};
const source = (text = "Answer"): ForwardSource => ({
  kind: "agent",
  content: { text, attachments: [], localArtifacts: [] },
});
const company: ForwardSource = {
  kind: "company",
  conversationId: "origin",
  message: {
    message_id: "message",
    seq: "1",
    sender_id: "peer",
    type: "file",
    text: "Please review",
    recalled: false,
    created_at: "2026-10-17T00:00:00Z",
  },
};
const delivery = (value: ForwardSource = source(), draft?: ForwardDraft) =>
  new ForwardDelivery(value, new AbortController().signal, draft);

beforeEach(() => {
  vi.clearAllMocks();
  mock.identity = "account:session";
  mock.api.send.mockResolvedValue({});
  mock.api.direct.mockResolvedValue({ conversation_id: "private" });
  mock.api.forward.mockResolvedValue({});
  mock.api.message.mockResolvedValue({
    ...company.message,
    attachments: [
      {
        asset_id: "asset",
        filename: "report.pdf",
        mime: "application/pdf",
        purpose: "chat_file",
      },
    ],
  });
  mock.blob.mockResolvedValue(
    new Blob(["document"], { type: "application/pdf" }),
  );
  mock.upload.mockResolvedValue({
    path: "inbound/report.pdf",
    filename: "report.pdf",
    media_type: "application/pdf",
    access_url: "/api/agents/agent/workspace/download?path=inbound/report.pdf",
  });
  mock.create.mockResolvedValue({ thread_id: "thread" });
  mock.turn.mockResolvedValue(undefined);
  mock.file.mockResolvedValue("file-asset");
  mock.image.mockResolvedValue("image-asset");
  mock.wait.mockResolvedValue("local-asset");
});
afterEach(() => {
  delete window.__octopRuntime;
  vi.unstubAllGlobals();
});

it("splits long Unicode answers and attachment batches without dropping characters", () => {
  const text = "😀".repeat(8001);
  const parts = forwardMessageParts(
    text,
    Array.from({ length: 10 }, (_, i) => `asset-${i}`),
  );
  expect(parts.map((part) => part.text || "").join("")).toBe(text);
  expect(Array.from(parts[0].text!)).toHaveLength(8000);
  expect(parts.map((part) => part.asset_ids?.length || 0)).toEqual([
    0, 0, 9, 1,
  ]);
  expect(new Set(parts.map((part) => part.client_message_id)).size).toBe(4);
});

it("creates a direct chat for a colleague without requiring an existing conversation", async () => {
  await delivery().send({
    value: "contact:peer",
    label: "Peer",
    kind: "contact",
    id: "peer",
  });
  expect(mock.api.direct).toHaveBeenCalledWith("peer", expect.any(AbortSignal));
  expect(mock.api.send).toHaveBeenCalledWith(
    "private",
    expect.objectContaining({ text: "Answer" }),
    expect.any(AbortSignal),
  );
});

it("retries only the failed part with the same ID and does not resend completed targets", async () => {
  mock.api.send
    .mockResolvedValueOnce({})
    .mockRejectedValueOnce(new Error("connection lost"))
    .mockResolvedValue({});
  const forward = delivery(source("a".repeat(8001)));
  await expect(forward.send(group)).rejects.toThrow("connection lost");
  const failed = mock.api.send.mock.calls[1][1];
  await forward.send(group);
  await forward.send(group);
  expect(mock.api.send).toHaveBeenCalledTimes(3);
  expect(mock.api.send.mock.calls[2][1]).toEqual(failed);
});

it("copies an authenticated workspace document once and reuses its asset for all recipients", async () => {
  const value = source();
  if (value.kind !== "agent") throw new Error();
  value.content.attachments.push({
    url: "/api/agents/a/workspace/download?path=report.pdf",
    filename: "report.pdf",
    kind: "file",
  });
  const forward = delivery(value);
  await forward.send(group);
  await forward.send({ ...group, value: "conversation:other", id: "other" });
  expect(mock.blob).toHaveBeenCalledWith(
    "/agents/a/workspace/download?path=report.pdf",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(mock.file).toHaveBeenCalledTimes(1);
  expect(mock.api.send.mock.calls[0][1]).toMatchObject({
    text: "Answer",
    asset_ids: ["file-asset"],
  });
  expect(mock.api.send.mock.calls[0][1].client_message_id).not.toBe(
    mock.api.send.mock.calls[1][1].client_message_id,
  );
});

it("uploads hash-verified native originals and waits until ready before sending", async () => {
  const uploadArtifact = vi
    .fn()
    .mockResolvedValue({ upload_id: "upload", asset_id: "local-asset" });
  window.__octopRuntime = {
    version: 1,
    uploadArtifact,
  } as unknown as DesktopRuntimeBridge;
  const value = source();
  if (value.kind !== "agent") throw new Error();
  const file = { path: "/work/image.png", sha256: "a".repeat(64), size: 42 };
  value.content.localArtifacts.push(file);
  await delivery(value).send(group);
  expect(uploadArtifact).toHaveBeenCalledWith({
    ...file,
    token: "current-token",
  });
  expect(mock.wait).toHaveBeenCalledWith(
    { upload_id: "upload", asset_id: "local-asset" },
    expect.any(AbortSignal),
  );
  expect(mock.api.send.mock.calls[0][1].asset_ids).toEqual(["local-asset"]);
});

it("uses the server forward operation for employee/group messages with stable retry IDs", async () => {
  mock.api.forward
    .mockRejectedValueOnce(new Error("lost"))
    .mockResolvedValue({});
  const forward = delivery(company);
  await expect(forward.send(group)).rejects.toThrow("lost");
  await forward.send(group);
  expect(mock.api.forward.mock.calls[1]).toEqual(
    mock.api.forward.mock.calls[0],
  );
  expect(mock.api.send).not.toHaveBeenCalled();
});

it("copies company files into the chosen server agent workspace and starts a new conversation", async () => {
  const result = await delivery(company).send(agent);
  expect(mock.api.message).toHaveBeenCalledWith(
    "origin",
    "message",
    expect.any(AbortSignal),
  );
  expect(mock.upload).toHaveBeenCalledWith(
    "agent",
    expect.objectContaining({ name: "report.pdf", type: "application/pdf" }),
  );
  expect(mock.turn.mock.calls[0].slice(0, 5)).toEqual([
    "thread",
    "Please review",
    "agent",
    "",
    [expect.objectContaining({ workspacePath: "inbound/report.pdf" })],
  ]);
  expect(result.path).toBe("/chat/agent/thread");
});

it("imports company files into the local workspace before starting a native agent turn", async () => {
  const importAttachment = vi.fn().mockResolvedValue({
    path: "/work/thread/inbound/report.pdf",
    size: 8,
    sha256: "a".repeat(64),
  });
  window.__octopRuntime = {
    version: 1,
    importAttachment,
  } as unknown as DesktopRuntimeBridge;
  await delivery(company).send(agent);
  expect(importAttachment).toHaveBeenCalledWith({
    asset_id: "asset",
    thread_id: "thread",
    filename: "report.pdf",
    token: "current-token",
  });
  expect(mock.turn.mock.calls[0][1]).toBe(
    "Please review\n\n[file: /work/thread/inbound/report.pdf]",
  );
  expect(mock.turn.mock.calls[0][4]).toEqual([
    expect.objectContaining({
      url: "",
      filename: "report.pdf",
      localFile: {
        path: "/work/thread/inbound/report.pdf",
        size: 8,
        sha256: "a".repeat(64),
      },
    }),
  ]);
  expect(mock.append.mock.calls[0][1].attachments).toEqual(
    mock.turn.mock.calls[0][4],
  );
  expect(mock.upload).not.toHaveBeenCalled();
});

it("refuses recalled originals before copying files or creating agent threads", async () => {
  mock.api.message.mockResolvedValue({ ...company.message, recalled: true });
  await expect(delivery(company).send(agent)).rejects.toMatchObject({
    code: "message_not_found",
  });
  expect(mock.blob).not.toHaveBeenCalled();
  expect(mock.create).not.toHaveBeenCalled();
});

it("stops on account changes before sharing any prepared file", async () => {
  const forward = delivery();
  mock.identity = "other:session";
  await expect(forward.send(group)).rejects.toMatchObject({
    name: "AbortError",
  });
  expect(mock.api.send).not.toHaveBeenCalled();
});

it("copies a local agent deliverable into the other agent's new project", async () => {
  const importAttachment = vi.fn().mockResolvedValue({
    path: "/work/thread/inbound/image.png",
    size: 42,
    sha256: "a".repeat(64),
  });
  window.__octopRuntime = {
    version: 1,
    importAttachment,
  } as unknown as DesktopRuntimeBridge;
  const value = source();
  if (value.kind !== "agent") throw new Error();
  value.content.localArtifacts.push({
    path: "/work/old/image.png",
    size: 42,
    sha256: "a".repeat(64),
  });
  await delivery(value).send(agent);
  expect(importAttachment).toHaveBeenCalledWith({
    path: "/work/old/image.png",
    sha256: "a".repeat(64),
    thread_id: "thread",
    token: "current-token",
  });
  expect(mock.turn.mock.calls[0][1]).toBe(
    "Answer\n\n[file: /work/thread/inbound/image.png]",
  );
  expect(mock.turn.mock.calls[0][4]).toEqual([
    expect.objectContaining({
      url: "",
      filename: "image.png",
      kind: "image",
      localFile: {
        path: "/work/thread/inbound/image.png",
        size: 42,
        sha256: "a".repeat(64),
      },
    }),
  ]);
  expect(mock.api.send).not.toHaveBeenCalled();
});

it("never sends the account token when fetching an external attachment", async () => {
  const fetch = vi.fn().mockResolvedValue({
    ok: true,
    blob: async () => new Blob(["data"], { type: "application/pdf" }),
  });
  vi.stubGlobal("fetch", fetch);
  const value = source();
  if (value.kind !== "agent") throw new Error();
  value.content.attachments.push({
    url: "https://example.com/api/agents/a/workspace/download?path=report.pdf",
    kind: "file",
  });
  await delivery(value).send(group);
  expect(mock.blob).not.toHaveBeenCalled();
  expect(fetch.mock.calls[0][1]).toEqual({
    signal: expect.any(AbortSignal),
    credentials: "omit",
    redirect: "error",
  });
});

it("sends only the chosen image and instructions together without copying other images or text", async () => {
  const value: ForwardSource = {
    kind: "agent",
    content: {
      text: "Descriptions of two images",
      attachments: ["first.png", "second.png", "report.pdf"].map(
        (filename) => ({
          filename,
          url: `/api/agents/a/workspace/download?path=${filename}`,
          kind: filename.endsWith("png") ? "image" : "file",
        }),
      ),
      localArtifacts: [],
    },
  };
  mock.blob.mockResolvedValue(new Blob(["image"], { type: "image/png" }));
  await delivery(value, {
    selection: {
      text: false,
      attachmentUrls: [value.content.attachments[1].url],
      localArtifactPaths: [],
    },
    note: "Review this image",
  }).send(group);
  expect(mock.blob).toHaveBeenCalledTimes(1);
  expect(mock.blob.mock.calls[0][0]).toContain("second.png");
  expect(mock.api.send).toHaveBeenCalledTimes(1);
  expect(mock.api.send.mock.calls[0][1]).toMatchObject({
    text: "Review this image",
    asset_ids: ["image-asset"],
  });
});

it("imports only a selected company file and includes instructions in the same native agent turn", async () => {
  const attachments = ["one", "two"].map((asset_id) => ({
    asset_id,
    state: "ready",
    purpose: "chat_file" as const,
    filename: `${asset_id}.pdf`,
    mime: "application/pdf",
  }));
  const value: ForwardSource = {
    ...company,
    message: { ...company.message, attachments },
  };
  mock.api.message.mockResolvedValue(value.message);
  const importAttachment = vi.fn().mockResolvedValue({
    path: "/work/thread/inbound/two.pdf",
    size: 8,
    sha256: "a".repeat(64),
  });
  window.__octopRuntime = {
    version: 1,
    importAttachment,
  } as unknown as DesktopRuntimeBridge;
  await delivery(value, {
    selection: {
      text: false,
      attachmentUrls: ["/api/im/media/two?kind=original"],
      localArtifactPaths: [],
    },
    note: "整理文件并列出待办",
  }).send(agent);
  expect(importAttachment).toHaveBeenCalledTimes(1);
  expect(importAttachment.mock.calls[0][0]).toMatchObject({
    asset_id: "two",
    thread_id: "thread",
  });
  expect(mock.turn).toHaveBeenCalledTimes(1);
  expect(mock.turn.mock.calls[0][1]).toBe(
    "整理文件并列出待办\n\n[file: /work/thread/inbound/two.pdf]",
  );
  expect(mock.create).toHaveBeenCalledTimes(1);
});

it("sends the company file and appended instructions in one server agent request", async () => {
  const value: ForwardSource = {
    ...company,
    message: await mock.api.message(),
  };
  await delivery(value, {
    selection: allForwardSelection(forwardSourceContent(value)),
    note: "Summarize the report",
  }).send(agent);
  expect(mock.turn).toHaveBeenCalledTimes(1);
  expect(mock.turn.mock.calls[0].slice(0, 5)).toEqual([
    "thread",
    "Please review\n\nSummarize the report",
    "agent",
    "",
    [expect.objectContaining({ filename: "report.pdf" })],
  ]);
});

it("forwards a company subset with a note as one new message and reuses the copy on retry", async () => {
  const value: ForwardSource = {
    ...company,
    message: await mock.api.message(),
  };
  const draft = {
    selection: allForwardSelection(forwardSourceContent(value)),
    note: "Please handle this",
  };
  const forward = delivery(value, draft);
  mock.api.send.mockRejectedValueOnce(new Error("lost")).mockResolvedValue({});
  await expect(forward.send(group)).rejects.toThrow("lost");
  await forward.send(group);
  expect(mock.api.forward).not.toHaveBeenCalled();
  expect(mock.file).toHaveBeenCalledTimes(1);
  expect(mock.api.send.mock.calls[0][1]).toEqual(
    mock.api.send.mock.calls[1][1],
  );
  expect(mock.api.send.mock.calls[1][1]).toMatchObject({
    text: "Please review\n\nPlease handle this",
    asset_ids: ["file-asset"],
  });
});

it("preserves server forwarding for the complete company message without a note", async () => {
  const value: ForwardSource = {
    ...company,
    message: await mock.api.message(),
  };
  await delivery(value, {
    selection: allForwardSelection(forwardSourceContent(value)),
    note: "  ",
  }).send(group);
  expect(mock.api.forward).toHaveBeenCalledTimes(1);
  expect(mock.file).not.toHaveBeenCalled();
  expect(mock.api.send).not.toHaveBeenCalled();
});

it("does not silently replace a selected file when the original attachments change", async () => {
  await expect(
    delivery(company, {
      selection: {
        text: false,
        attachmentUrls: ["/api/im/media/missing?kind=original"],
        localArtifactPaths: [],
      },
      note: "Summarize",
    }).send(agent),
  ).rejects.toMatchObject({ code: "forward_content_changed" });
  expect(mock.create).not.toHaveBeenCalled();
  expect(mock.upload).not.toHaveBeenCalled();
});

it("rejects a note without selected original content before creating a conversation", async () => {
  await expect(
    delivery(source(), {
      selection: { text: false, attachmentUrls: [], localArtifactPaths: [] },
      note: "Summarize",
    }).send(agent),
  ).rejects.toMatchObject({ code: "invalid_request" });
  expect(mock.create).not.toHaveBeenCalled();
});

it("freezes the draft so a retry cannot send edited instructions with a previous receipt", async () => {
  const value = source();
  const draft: ForwardDraft = {
    selection: allForwardSelection(forwardSourceContent(value)),
    note: "Original request",
  };
  const forward = delivery(value, draft);
  draft.note = "Changed request";
  draft.selection.text = false;
  await forward.send(group);
  expect(mock.api.send.mock.calls[0][1].text).toBe(
    "Answer\n\nOriginal request",
  );
});
