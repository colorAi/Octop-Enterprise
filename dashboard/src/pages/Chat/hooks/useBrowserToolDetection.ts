import { useEffect, useState } from "react";
import * as chatStore from "./chatStore";
import { isBrowserToolName } from "../constants";
import type { ChatMessage } from "./useChat";

export function useBrowserToolDetection(
  activeThreadId: string | null,
  messages: ChatMessage[],
  refreshBrowserSession: () => void,
) {
  const [hasBrowserTool, setHasBrowserTool] = useState(false);

  useEffect(() => {
    const used = messages.some((m) => isBrowserToolName(m.toolData?.name));
    if (used) setHasBrowserTool(true);
  }, [messages]);

  useEffect(() => {
    return chatStore.onToolEvent((event) => {
      if (
        event.sessionId === (activeThreadId ?? "") &&
        isBrowserToolName(event.toolName)
      ) {
        setHasBrowserTool(true);
        if (event.kind === "toolDone") {
          refreshBrowserSession();
        }
      }
    });
  }, [activeThreadId, refreshBrowserSession]);

  return { hasBrowserTool, setHasBrowserTool };
}
