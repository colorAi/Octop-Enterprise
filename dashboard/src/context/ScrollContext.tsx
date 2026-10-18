/**
 * ScrollContext — optional cross-component scroll state sharing.
 *
 * Use this when multiple components (e.g., a floating action button outside
 * MessageList) need to react to scroll state or trigger scrolls without
 * prop-drilling through the component tree.
 *
 * Usage:
 *   // In a parent component (e.g., ChatPage):
 *   <ScrollProvider>
 *     <MessageList />
 *     <SomeFloatingButton />  // can call resumeAutoScroll() from here
 *   </ScrollProvider>
 *
 *   // In any child:
 *   const { isAtBottom, autoScrollEnabled, resumeAutoScroll } = useScrollContext();
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";

/* ── Types ──────────────────────────────────────────────────────────────── */

export interface ScrollContextValue {
  /** Whether the message list is currently at the bottom. */
  isAtBottom: boolean;

  /** Whether programmatic auto-scroll is enabled. */
  autoScrollEnabled: boolean;

  /** Scroll to the bottom immediately. Pass `instant=true` to skip animation. */
  scrollToBottom: (instant?: boolean) => void;

  /** Re-enable auto-scroll and scroll to the bottom. */
  resumeAutoScroll: () => void;
}

/**
 * Internal context value — extends public interface with registration methods.
 * Only used by the scroll implementation (e.g., MessageList / useAutoScroll).
 */
export interface ScrollContextInternal extends ScrollContextValue {
  _register: (actions: ScrollActions) => void;
  _update: (state: ScrollState) => void;
}

interface ScrollActions {
  scrollToBottom: (instant?: boolean) => void;
  resumeAutoScroll: () => void;
}

interface ScrollState {
  isAtBottom: boolean;
  autoScrollEnabled: boolean;
}

/* ── Context creation ───────────────────────────────────────────────────── */

const ScrollContext = createContext<ScrollContextInternal | null>(null);

/* ── Provider ───────────────────────────────────────────────────────────── */

interface ScrollProviderProps {
  children: React.ReactNode;
}

/**
 * ScrollProvider wraps a chat view and makes scroll state available
 * to any descendant without prop drilling.
 */
export function ScrollProvider({ children }: ScrollProviderProps) {
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [autoScrollEnabled, setAutoScrollEnabled] = useState(true);

  // Store the imperative handles provided by the inner MessageList.
  const actionsRef = useRef<ScrollActions>({
    scrollToBottom: () => {},
    resumeAutoScroll: () => {},
  });

  const _register = useCallback((actions: ScrollActions) => {
    actionsRef.current = actions;
  }, []);

  const _update = useCallback((state: ScrollState) => {
    setIsAtBottom(state.isAtBottom);
    setAutoScrollEnabled(state.autoScrollEnabled);
  }, []);

  const scrollToBottom = useCallback((instant?: boolean) => {
    actionsRef.current.scrollToBottom(instant);
  }, []);

  const resumeAutoScroll = useCallback(() => {
    actionsRef.current.resumeAutoScroll();
  }, []);

  const value = useMemo<ScrollContextInternal>(
    () => ({
      isAtBottom,
      autoScrollEnabled,
      scrollToBottom,
      resumeAutoScroll,
      _register,
      _update,
    }),
    [
      isAtBottom,
      autoScrollEnabled,
      scrollToBottom,
      resumeAutoScroll,
      _register,
      _update,
    ],
  );

  return (
    <ScrollContext.Provider value={value}>{children}</ScrollContext.Provider>
  );
}

/* ── Consumer hook ──────────────────────────────────────────────────────── */

/**
 * Access scroll state and actions from any component inside a ScrollProvider.
 *
 * @throws {Error} if used outside of a ScrollProvider.
 */
export function useScrollContext(): ScrollContextValue {
  const ctx = useContext(ScrollContext);
  if (!ctx) {
    throw new Error("useScrollContext must be used inside a <ScrollProvider>.");
  }
  return ctx;
}

/**
 * Safe version that returns null if no ScrollProvider is present.
 * Useful for components that optionally participate in scroll coordination.
 */
export function useScrollContextSafe(): ScrollContextValue | null {
  return useContext(ScrollContext);
}

/**
 * Access the internal context value with `_register` and `_update`.
 * Only for use by the scroll implementation (e.g., MessageList).
 */
export function useScrollContextInternal(): ScrollContextInternal {
  const ctx = useContext(ScrollContext);
  if (!ctx) {
    throw new Error(
      "useScrollContextInternal must be used inside a <ScrollProvider>.",
    );
  }
  return ctx;
}
