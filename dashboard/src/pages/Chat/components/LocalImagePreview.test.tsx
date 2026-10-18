import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DesktopRuntimeBridge } from "@/api/desktopRuntime";
import {
  LocalImagePreview,
  isLocalPreviewImage,
  isNativePreviewData,
} from "./LocalImagePreview";
const file = { path: "/work/image.png", size: 68, sha256: "a".repeat(64) };
const png =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==";
function bridge(previewImage?: DesktopRuntimeBridge["previewImage"]) {
  window.__octopRuntime = {
    version: 1,
    status: vi.fn(),
    start: vi.fn(),
    cancel: vi.fn(),
    reveal: vi.fn(),
    subscribe: vi.fn(),
    previewImage,
  };
  return window.__octopRuntime;
}
afterEach(() => {
  delete window.__octopRuntime;
  vi.unstubAllGlobals();
});
describe("local image preview", () => {
  it("accepts raster filenames and PNG derivatives only", () => {
    expect(isLocalPreviewImage("/x/a.WEBP")).toBe(true);
    for (const value of ["/x/a.svg", "/x/a.html", "/x/a.png?url=x"])
      expect(isLocalPreviewImage(value)).toBe(false);
    expect(isNativePreviewData(png)).toBe(true);
    for (const value of [
      "https://external/image.png",
      "data:image/svg+xml;base64,PHN2Zz4=",
      "file:///work/image.png",
      null,
    ])
      expect(isNativePreviewData(value)).toBe(false);
  });
  it("reads an exact local receipt and never fetches or generates", async () => {
    const preview = vi
      .fn()
      .mockResolvedValue({ data_url: png, width: 1, height: 1 });
    const runtime = bridge(preview);
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    render(<LocalImagePreview file={file} scope="account-1" />);
    expect(
      await screen.findByRole("img", { name: "本地图片预览：image.png" }),
    ).toHaveAttribute("src", png);
    expect(preview).toHaveBeenCalledWith({
      path: file.path,
      sha256: file.sha256,
    });
    fireEvent.click(
      screen.getByRole("img", { name: "本地图片预览：image.png" }),
    );
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    expect(runtime.start).not.toHaveBeenCalled();
  });
  it("keeps the reserved thumbnail frame when a delayed preview arrives", async () => {
    let resolvePreview!: (value: {
      data_url: string;
      width: number;
      height: number;
    }) => void;
    const pending = new Promise<{
      data_url: string;
      width: number;
      height: number;
    }>((resolve) => {
      resolvePreview = resolve;
    });
    bridge(vi.fn().mockReturnValue(pending));
    render(<LocalImagePreview file={file} scope="account-1" />);
    const frame = screen.getByText("正在读取本机图片…").parentElement;
    await act(async () => {
      resolvePreview({ data_url: png, width: 1, height: 1 });
      await pending;
    });
    expect(frame).toContainElement(
      screen.getByRole("img", { name: "本地图片预览：image.png" }),
    );
  });
  it("explains the installed native capability separately from the updated webpage", () => {
    bridge();
    render(<LocalImagePreview file={file} scope="account-1" />);
    expect(
      screen.getByText(/网页已更新；当前客户端尚不支持本地图片预览/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });
  it("does not attempt to read files in a browser", () => {
    render(<LocalImagePreview file={file} scope="account-1" />);
    expect(screen.getByText(/当前网页不能直接预览/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });
  it("rejects external image sources and retries only the local preview", async () => {
    const preview = vi
      .fn()
      .mockResolvedValueOnce({ data_url: "https://external/image.png" })
      .mockResolvedValue({ data_url: png, width: 1, height: 1 });
    const runtime = bridge(preview);
    render(<LocalImagePreview file={file} scope="account-1" />);
    expect(
      await screen.findByText("本机返回了无效的图片预览"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重试预览" }));
    expect(
      await screen.findByRole("img", { name: "本地图片预览：image.png" }),
    ).toHaveAttribute("src", png);
    expect(preview).toHaveBeenCalledTimes(2);
    expect(runtime.start).not.toHaveBeenCalled();
  });
  it("discards delayed old-account results and hides the previous account image immediately", async () => {
    let resolveOld: (value: {
      data_url: string;
      width: number;
      height: number;
    }) => void = () => {};
    const pending = new Promise<{
      data_url: string;
      width: number;
      height: number;
    }>((resolve) => {
      resolveOld = resolve;
    });
    const preview = vi
      .fn()
      .mockReturnValueOnce(pending)
      .mockResolvedValueOnce({ data_url: png, width: 1, height: 1 })
      .mockReturnValue(new Promise(() => {}));
    bridge(preview);
    const view = render(<LocalImagePreview file={file} scope="account-1" />);
    view.rerender(<LocalImagePreview file={file} scope="account-2" />);
    expect(
      await screen.findByRole("img", { name: "本地图片预览：image.png" }),
    ).toHaveAttribute("src", png);
    await act(async () => {
      resolveOld({
        data_url: "data:image/png;base64,AAAA",
        width: 1,
        height: 1,
      });
      await pending;
    });
    expect(
      screen.getByRole("img", { name: "本地图片预览：image.png" }),
    ).toHaveAttribute("src", png);
    view.rerender(
      <LocalImagePreview file={file} scope="account-3/new-grant" />,
    );
    expect(screen.queryByRole("img")).toBeNull();
    await waitFor(() => expect(preview).toHaveBeenCalledTimes(3));
  });
});
