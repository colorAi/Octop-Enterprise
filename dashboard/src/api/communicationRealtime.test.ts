import { afterEach, describe, expect, it, vi } from "vitest";
import { connectCommunicationRealtime } from "./communicationRealtime";
import { communicationApi } from "./modules/communication";
vi.mock("./config", () => ({
  getWsUrl: () => "ws://localhost/api/im/realtime",
}));
class Socket {
  static OPEN = 1;
  static instances: Socket[] = [];
  readyState = 1;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = () => this.onclose?.();
  constructor(url: string) {
    this.url = url;
    Socket.instances.push(this);
  }
}
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Socket.instances = [];
});
describe("communication realtime", () => {
  it("uses new tickets on reconnect, wakes on open, and stops cleanly", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("WebSocket", Socket);
    const ticket = vi
      .spyOn(communicationApi, "ticket")
      .mockResolvedValueOnce({ ticket: "first", expires_at: 1 })
      .mockResolvedValue({ ticket: "second", expires_at: 2 });
    const wake = vi.fn();
    const controller = new AbortController();
    const stop = connectCommunicationRealtime(
      controller.signal,
      wake,
      vi.fn(),
      vi.fn(),
    );
    await Promise.resolve();
    expect(Socket.instances[0].url).toBe(
      "ws://localhost/api/im/realtime?ticket=first",
    );
    Socket.instances[0].onopen?.();
    expect(wake).toHaveBeenCalledTimes(1);
    Socket.instances[0].onmessage?.({ data: '{"type":"sync.available"}' });
    expect(wake).toHaveBeenCalledTimes(2);
    Socket.instances[0].close();
    await vi.advanceTimersByTimeAsync(1000);
    expect(ticket).toHaveBeenCalledTimes(2);
    expect(Socket.instances[1].url).toContain("ticket=second");
    Socket.instances[1].onopen?.();
    expect(wake).toHaveBeenCalledTimes(3);
    Socket.instances[1].onmessage?.({ data: '{"type":"presence.changed"}' });
    expect(wake).toHaveBeenCalledTimes(4);
    stop();
    await vi.advanceTimersByTimeAsync(60000);
    expect(ticket).toHaveBeenCalledTimes(2);
  });
});
