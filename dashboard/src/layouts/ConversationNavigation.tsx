import { createContext, useContext, useRef, type ReactNode } from "react";
import { useLocation } from "react-router-dom";

export function isConversationPath(path: string) {
  return ["/chat", "/contacts", "/messages"].some(
    (root) => path === root || path.startsWith(root + "/"),
  );
}

const ConversationPaths = createContext({ agent: "/chat", company: "" });

/** Remember the last Agent and company locations for the independent nav entries. */
export function ConversationNavigationProvider({
  children,
}: {
  children: ReactNode;
}) {
  const { pathname, search } = useLocation();
  const paths = useRef({ agent: "/chat", company: "" });
  if (isConversationPath(pathname)) {
    const key =
      pathname === "/chat" || pathname.startsWith("/chat/")
        ? "agent"
        : "company";
    paths.current[key] = pathname + search;
  }
  return (
    <ConversationPaths.Provider value={{ ...paths.current }}>
      {children}
    </ConversationPaths.Provider>
  );
}

export function useConversationNavigation() {
  const paths = useContext(ConversationPaths);
  return (path: string) => {
    if (path === "/chat") return paths.agent;
    if (path === "/contacts" || path === "/messages")
      return paths.company || path;
    return path;
  };
}
