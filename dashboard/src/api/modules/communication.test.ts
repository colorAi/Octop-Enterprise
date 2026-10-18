import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  communicationApi,
  CommunicationError,
  uploadCommunicationImage,
  uploadCommunicationFile,
} from "./communication";
import { request } from "../request";
vi.mock("../request", () => ({ request: vi.fn(), requestBlob: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
describe("communication API", () => {
  it("unwraps data while keeping decimal sequences and encoded cursors", async () => {
    vi.mocked(request).mockResolvedValue({
      request_id: "r",
      data: { events: [], cursor: "9007199254740993" },
    });
    const value = await communicationApi.sync("a+b/c=");
    expect(value.cursor).toBe("9007199254740993");
    expect(request).toHaveBeenCalledWith(
      "/im/sync?cursor=a%2Bb%2Fc%3D&limit=200",
      expect.objectContaining({ method: "GET" }),
    );
  });
  it("keeps stable error codes and retryability", async () => {
    vi.mocked(request).mockRejectedValue(
      new Error(
        'Request failed: 403 Forbidden - {"error":{"code":"send_not_allowed","message":"denied","retryable":false}}',
      ),
    );
    await expect(communicationApi.direct("b")).rejects.toMatchObject({
      code: "send_not_allowed",
      status: 403,
      retryable: false,
    });
  });
  it("hashes image bytes without secure-context WebCrypto and sends only ready assets", async () => {
    const bytes = new TextEncoder().encode("abc");
    const file = new File([bytes], "test.png", { type: "image/png" });
    Object.defineProperty(file, "arrayBuffer", {
      value: async () => bytes.buffer,
    });
    const init = vi
      .spyOn(communicationApi, "uploadInit")
      .mockResolvedValue({ upload_id: "u", asset_id: "a" });
    const send = vi
      .spyOn(communicationApi, "uploadBytes")
      .mockResolvedValue(undefined);
    vi.spyOn(communicationApi, "uploadComplete").mockResolvedValue(undefined);
    vi.spyOn(communicationApi, "uploadStatus").mockResolvedValue({
      upload_id: "u",
      asset_id: "a",
      state: "ready",
      last_error: "",
    });
    expect(
      await uploadCommunicationImage(file, new AbortController().signal),
    ).toBe("a");
    expect(init).toHaveBeenCalledWith(
      {
        purpose: "chat_image",
        mime: "image/png",
        size_bytes: 3,
        sha256:
          "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
      },
      expect.any(AbortSignal),
    );
    expect(send).toHaveBeenCalledWith("u", file, expect.any(AbortSignal));
    vi.restoreAllMocks();
  });
  it("rejects unsupported image uploads before making a request", async () => {
    await expect(
      uploadCommunicationImage(
        new File(["x"], "x.svg", { type: "image/svg+xml" }),
        new AbortController().signal,
      ),
    ).rejects.toBeInstanceOf(CommunicationError);
    expect(request).not.toHaveBeenCalled();
  });
});

it("uploads documents with their original filename and a safe MIME fallback", async () => {
  const file = new File(["abc"], "报告.txt");
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode("abc").buffer,
  });
  const init = vi
    .spyOn(communicationApi, "uploadInit")
    .mockResolvedValue({ upload_id: "u", asset_id: "a" });
  vi.spyOn(communicationApi, "uploadBytes").mockResolvedValue(undefined);
  vi.spyOn(communicationApi, "uploadComplete").mockResolvedValue(undefined);
  vi.spyOn(communicationApi, "uploadStatus").mockResolvedValue({
    upload_id: "u",
    asset_id: "a",
    state: "ready",
    last_error: "",
  });
  expect(
    await uploadCommunicationFile(file, new AbortController().signal),
  ).toBe("a");
  expect(init).toHaveBeenCalledWith(
    expect.objectContaining({
      purpose: "chat_file",
      filename: "报告.txt",
      mime: "application/octet-stream",
      size_bytes: 3,
    }),
    expect.any(AbortSignal),
  );
  vi.restoreAllMocks();
});
it("rejects empty and oversized documents before initiating an upload", async () => {
  const file = new File(["x"], "big.zip");
  Object.defineProperty(file, "size", { value: 50 * 1024 * 1024 + 1 });
  for (const rejected of [file, new File([], "empty.txt")])
    await expect(
      uploadCommunicationFile(rejected, new AbortController().signal),
    ).rejects.toMatchObject({ code: "invalid_file" });
  expect(request).not.toHaveBeenCalled();
});
