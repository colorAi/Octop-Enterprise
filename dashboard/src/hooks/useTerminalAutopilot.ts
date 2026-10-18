import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../pages/Chat/hooks/useChat";
import { deriveMessageContent } from "../pages/Chat/utils/messageContent";
import { extractBashBlocks } from "../utils/shellCodeBlock";
import { outputLooksFailed } from "../pages/Control/Terminal/terminalContext";
import { waitForOutputQuiet } from "../pages/Control/Terminal/terminalOutputBuffer";

export type AutopilotStatus =
  | "idle"
  | "planning"
  | "running"
  | "paused"
  | "done"
  | "failed";

export interface AutopilotStep {
  id: string;
  command: string;
  status: "pending" | "running" | "done" | "failed" | "skipped";
}

interface UseTerminalAutopilotOptions {
  send: (text: string) => void;
  messages: ChatMessage[];
  isStreaming: boolean;
  onExecuteCommand: (cmd: string) => void;
  activeTerminalSessionId: string | null;
  formatMessage: (userText: string, autopilot?: boolean) => string;
  getRecentOutput: () => string;
  snapshotOutput: () => string;
  stepQuietMs?: number;
  stepTimeoutMs?: number;
}

export function useTerminalAutopilot({
  send,
  messages,
  isStreaming,
  onExecuteCommand,
  activeTerminalSessionId,
  formatMessage,
  getRecentOutput,
  snapshotOutput,
  stepQuietMs = 1500,
  stepTimeoutMs = 60_000,
}: UseTerminalAutopilotOptions) {
  const [status, setStatus] = useState<AutopilotStatus>("idle");
  const [steps, setSteps] = useState<AutopilotStep[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [goal, setGoal] = useState("");

  const abortRef = useRef<AbortController | null>(null);
  const pausedRef = useRef(false);
  const stepsRef = useRef<AutopilotStep[]>([]);
  const planningMsgCountRef = useRef(0);

  stepsRef.current = steps;

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    pausedRef.current = false;
    setStatus("idle");
    setSteps([]);
    setCurrentIndex(0);
    planningMsgCountRef.current = 0;
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    pausedRef.current = false;
    setStatus("idle");
    setSteps((prev) =>
      prev.map((s) =>
        s.status === "running" ? { ...s, status: "failed" as const } : s,
      ),
    );
  }, []);

  const runFromIndex = useCallback(
    async (startIndex: number) => {
      if (!activeTerminalSessionId) return;

      const abort = new AbortController();
      abortRef.current = abort;
      pausedRef.current = false;
      setStatus("running");

      for (let i = startIndex; i < stepsRef.current.length; i++) {
        if (abort.signal.aborted) {
          if (pausedRef.current) setStatus("paused");
          return;
        }

        const step = stepsRef.current[i];
        if (step.status === "skipped" || step.status === "done") continue;

        setCurrentIndex(i);
        setSteps((prev) =>
          prev.map((s, idx) => (idx === i ? { ...s, status: "running" } : s)),
        );

        const snap = snapshotOutput();
        onExecuteCommand(step.command);

        const { output: fullOutput } = await waitForOutputQuiet(
          getRecentOutput,
          {
            quietMs: stepQuietMs,
            timeoutMs: stepTimeoutMs,
            signal: abort.signal,
          },
        );

        if (abort.signal.aborted) {
          if (pausedRef.current) {
            setSteps((prev) =>
              prev.map((s, idx) =>
                idx === i && s.status === "running"
                  ? { ...s, status: "pending" }
                  : s,
              ),
            );
            setStatus("paused");
          }
          return;
        }

        const stepOutput =
          fullOutput.startsWith(snap) && fullOutput.length >= snap.length
            ? fullOutput.slice(snap.length)
            : fullOutput;

        if (outputLooksFailed(stepOutput)) {
          setSteps((prev) =>
            prev.map((s, idx) => (idx === i ? { ...s, status: "failed" } : s)),
          );
          setStatus("failed");
          return;
        }

        setSteps((prev) =>
          prev.map((s, idx) => (idx === i ? { ...s, status: "done" } : s)),
        );
      }

      setStatus("done");
    },
    [
      activeTerminalSessionId,
      getRecentOutput,
      onExecuteCommand,
      snapshotOutput,
      stepQuietMs,
      stepTimeoutMs,
    ],
  );

  const pause = useCallback(() => {
    if (status !== "running") return;
    pausedRef.current = true;
    abortRef.current?.abort();
    setStatus("paused");
  }, [status]);

  const resume = useCallback(() => {
    if (status !== "paused") return;
    pausedRef.current = false;
    void runFromIndex(currentIndex);
  }, [status, currentIndex, runFromIndex]);

  const startPlanning = useCallback(() => {
    const text = goal.trim();
    if (!text || isStreaming || !activeTerminalSessionId) return;
    abortRef.current?.abort();
    pausedRef.current = false;
    planningMsgCountRef.current = messages.length;
    setSteps([]);
    setCurrentIndex(0);
    setStatus("planning");
    send(formatMessage(text, true));
  }, [
    goal,
    isStreaming,
    activeTerminalSessionId,
    messages.length,
    send,
    formatMessage,
  ]);

  // Parse plan when assistant finishes replying
  useEffect(() => {
    if (status !== "planning" || isStreaming) return;

    const newMessages = messages.slice(planningMsgCountRef.current);
    const lastAssistant = [...newMessages]
      .reverse()
      .find((m) => m.role === "assistant");
    if (!lastAssistant) return;

    const { textContent } = deriveMessageContent(lastAssistant);
    const blocks = extractBashBlocks(textContent);
    if (blocks.length === 0) {
      setStatus("failed");
      return;
    }

    const planned: AutopilotStep[] = blocks.map((command, idx) => ({
      id: `step-${idx}`,
      command,
      status: "pending",
    }));
    setSteps(planned);
    stepsRef.current = planned;
    void runFromIndex(0);
  }, [status, isStreaming, messages, runFromIndex]);

  const retryStep = useCallback(() => {
    const idx = currentIndex;
    setSteps((prev) =>
      prev.map((s, i) => (i === idx ? { ...s, status: "pending" } : s)),
    );
    void runFromIndex(idx);
  }, [currentIndex, runFromIndex]);

  const skipStep = useCallback(() => {
    const idx = currentIndex;
    setSteps((prev) =>
      prev.map((s, i) => (i === idx ? { ...s, status: "skipped" } : s)),
    );
    void runFromIndex(idx + 1);
  }, [currentIndex, runFromIndex]);

  const isActive =
    status === "planning" || status === "running" || status === "paused";

  return {
    status,
    steps,
    currentIndex,
    goal,
    setGoal,
    startPlanning,
    pause,
    resume,
    stop,
    reset,
    retryStep,
    skipStep,
    isActive,
  };
}
