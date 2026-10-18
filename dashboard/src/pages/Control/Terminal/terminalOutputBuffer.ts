/** Strip common ANSI escape sequences from PTY output for autopilot heuristics. */
export function stripAnsi(text: string): string {
  const esc = String.fromCharCode(0x1b);
  const bell = String.fromCharCode(0x07);
  return text
    .replace(new RegExp(`${esc}\\[[0-9;?]*[ -/]*[@-~]`, "g"), "")
    .replace(new RegExp(`${esc}\\][^${bell}]*(?:${bell}|${esc}\\\\)`, "g"), "")
    .replace(/\r/g, "");
}

const DEFAULT_MAX_CHARS = 32_000;

export interface TerminalOutputBuffer {
  append: (sessionId: string, chunk: string) => void;
  getRecent: (sessionId: string) => string;
  clear: (sessionId: string) => void;
  snapshot: (sessionId: string) => string;
}

/** In-memory ring buffer per terminal session (stripped ANSI text). */
export function createTerminalOutputBuffer(
  maxChars = DEFAULT_MAX_CHARS,
): TerminalOutputBuffer {
  const store = new Map<string, string>();
  const snapshots = new Map<string, string>();

  const append = (sessionId: string, chunk: string) => {
    const cleaned = stripAnsi(chunk);
    if (!cleaned) return;
    const prev = store.get(sessionId) ?? "";
    const next = (prev + cleaned).slice(-maxChars);
    store.set(sessionId, next);
  };

  const getRecent = (sessionId: string) => store.get(sessionId) ?? "";

  const clear = (sessionId: string) => {
    store.set(sessionId, "");
    snapshots.delete(sessionId);
  };

  /** Remember current tail so callers can diff output after a command. */
  const snapshot = (sessionId: string) => {
    const current = getRecent(sessionId);
    snapshots.set(sessionId, current);
    return current;
  };

  return { append, getRecent, clear, snapshot };
}

export function outputSinceSnapshot(
  buffer: TerminalOutputBuffer,
  sessionId: string,
  snapshotText: string,
): string {
  const current = buffer.getRecent(sessionId);
  if (!snapshotText) return current;
  if (current.startsWith(snapshotText)) {
    return current.slice(snapshotText.length);
  }
  return current;
}

export function waitForOutputQuiet(
  getOutput: () => string,
  options?: {
    quietMs?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
  },
): Promise<{ timedOut: boolean; output: string }> {
  const quietMs = options?.quietMs ?? 1500;
  const timeoutMs = options?.timeoutMs ?? 60_000;
  const signal = options?.signal;

  return new Promise((resolve) => {
    let lastOutput = getOutput();
    let lastChangeAt = Date.now();
    const startedAt = Date.now();

    const tick = () => {
      if (signal?.aborted) {
        resolve({ timedOut: true, output: getOutput() });
        return;
      }
      const now = Date.now();
      const output = getOutput();
      if (output !== lastOutput) {
        lastOutput = output;
        lastChangeAt = now;
      }
      if (now - startedAt >= timeoutMs) {
        resolve({ timedOut: true, output });
        return;
      }
      if (now - lastChangeAt >= quietMs) {
        resolve({ timedOut: false, output });
        return;
      }
      setTimeout(tick, 200);
    };

    setTimeout(tick, 200);
  });
}
