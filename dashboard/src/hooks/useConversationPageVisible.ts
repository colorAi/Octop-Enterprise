import { createContext, useContext } from "react";

export const ConversationPageVisibility = createContext(true);

/** Hidden conversation tabs retain their UI, but must not act as the open page. */
export function useConversationPageVisible(): boolean {
  return useContext(ConversationPageVisibility);
}
