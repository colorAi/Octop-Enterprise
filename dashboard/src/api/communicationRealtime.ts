import { getWsUrl } from "./config";
import { communicationApi } from "./modules/communication";

export type RealtimeStatus = "connecting" | "online" | "retrying" | "offline";
/** Notification-only WS. Each reconnect receives a fresh one-use ticket. */
export function connectCommunicationRealtime(
  signal: AbortSignal,
  onWake: () => void,
  onStatus: (status: RealtimeStatus) => void,
  onError: (error: unknown) => void,
) {
  let socket: WebSocket | null = null;
  let retry: ReturnType<typeof setTimeout> | undefined;
  let ping: ReturnType<typeof setInterval> | undefined;
  let failures = 0;
  let stopped = false;
  const stop = () => {
    stopped = true;
    signal.removeEventListener("abort", stop);
    if (retry) clearTimeout(retry);
    if (ping) clearInterval(ping);
    socket?.close();
    socket = null;
  };
  signal.addEventListener("abort", stop, { once: true });
  const schedule = () => {
    if (signal.aborted || stopped) return;
    onStatus("retrying");
    retry = setTimeout(
      () => void open(),
      Math.min(30_000, 1000 * 2 ** Math.min(failures++, 5)),
    );
  };
  async function open() {
    if (signal.aborted || stopped) return;
    onStatus("connecting");
    try {
      const value = await communicationApi.ticket(signal);
      if (signal.aborted || stopped) return;
      const ws = new WebSocket(
        `${getWsUrl("/im/realtime")}?ticket=${encodeURIComponent(
          value.ticket,
        )}`,
      );
      socket = ws;
      ws.onopen = () => {
        if (signal.aborted || stopped) {
          ws.close();
          return;
        }
        failures = 0;
        onStatus("online");
        onWake();
        ping = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN)
            ws.send(JSON.stringify({ type: "ping" }));
        }, 25_000);
      };
      ws.onmessage = (event) => {
        if (signal.aborted || stopped) return;
        try {
          if (
            ["sync.available", "presence.changed"].includes(
              JSON.parse(String(event.data)).type,
            )
          )
            onWake();
        } catch {
          /* invalid notification contains no state */
        }
      };
      ws.onclose = () => {
        if (ping) clearInterval(ping);
        ping = undefined;
        if (socket === ws) socket = null;
        schedule();
      };
      ws.onerror = () => ws.close();
    } catch (error) {
      if (!signal.aborted && !stopped) {
        onError(error);
        schedule();
      }
    }
  }
  void open();
  return stop;
}
