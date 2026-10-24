import {
  useCallback,
  useEffect,
  useRef,
  type ClipboardEvent,
  type CompositionEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";

interface BrowserKeyboardInputOptions {
  enabled: boolean;
  onEvent: (event: Record<string, unknown>) => void;
}

function keyEvent(event: KeyboardEvent<HTMLTextAreaElement>) {
  return {
    key: event.key,
    code: event.code,
    keyCode: event.keyCode,
    location: event.location,
    repeat: event.repeat,
    altKey: event.altKey,
    ctrlKey: event.ctrlKey,
    metaKey: event.metaKey,
    shiftKey: event.shiftKey,
  };
}

/** A native editable target lets the local IME compose before sending text. */
export function useBrowserKeyboardInput({
  enabled,
  onEvent,
}: BrowserKeyboardInputOptions) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const committedRef = useRef<string | null>(null);
  const commitTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const pressedRef = useRef(new Map<string, ReturnType<typeof keyEvent>>());

  const releaseKeys = useCallback(() => {
    for (const event of pressedRef.current.values()) {
      onEvent({
        ...event,
        type: "keyup",
        altKey: false,
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
      });
    }
    pressedRef.current.clear();
    composingRef.current = false;
    committedRef.current = null;
    clearTimeout(commitTimerRef.current);
    if (inputRef.current) inputRef.current.value = "";
  }, [onEvent]);

  useEffect(() => {
    if (!enabled) {
      releaseKeys();
      inputRef.current?.blur();
    }
    return releaseKeys;
  }, [enabled, releaseKeys]);

  const focusInput = useCallback(() => {
    if (enabled) inputRef.current?.focus({ preventScroll: true });
  }, [enabled]);

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!enabled) return;
    event.stopPropagation();
    if (
      composingRef.current ||
      event.nativeEvent.isComposing ||
      event.keyCode === 229 ||
      event.key === "Process" ||
      event.key === "Dead"
    ) {
      return;
    }
    committedRef.current = null;
    // Let the OS deliver the local clipboard through the native paste event.
    if (
      ((event.ctrlKey || event.metaKey) &&
        !event.altKey &&
        event.key.toLowerCase() === "v") ||
      (event.shiftKey && event.key === "Insert")
    ) {
      return;
    }
    const payload = keyEvent(event);
    pressedRef.current.set(event.code || event.key, payload);
    onEvent({ type: "keydown", ...payload });
    const textInput =
      event.key.length === 1 &&
      ((!event.ctrlKey && !event.metaKey) ||
        event.getModifierState("AltGraph"));
    if (!textInput) event.preventDefault();
  };

  const onKeyUp = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!enabled) return;
    event.stopPropagation();
    if (pressedRef.current.delete(event.code || event.key)) {
      onEvent({ type: "keyup", ...keyEvent(event) });
    }
  };

  const onInput = (event: FormEvent<HTMLTextAreaElement>) => {
    if (!enabled) return;
    event.stopPropagation();
    if (composingRef.current || (event.nativeEvent as InputEvent).isComposing) {
      return;
    }
    const text = event.currentTarget.value;
    event.currentTarget.value = "";
    // WebKit may send a final input after compositionend; commit only once.
    if (text && text !== committedRef.current) onEvent({ type: "type", text });
    committedRef.current = null;
  };

  const onCompositionStart = () => {
    composingRef.current = true;
    committedRef.current = null;
  };

  const onCompositionEnd = (event: CompositionEvent<HTMLTextAreaElement>) => {
    composingRef.current = false;
    event.currentTarget.value = "";
    if (!enabled || !event.data) return;
    onEvent({ type: "type", text: event.data });
    committedRef.current = event.data;
    clearTimeout(commitTimerRef.current);
    commitTimerRef.current = setTimeout(() => {
      committedRef.current = null;
    }, 0);
  };

  const onPaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (!enabled) return;
    event.preventDefault();
    event.stopPropagation();
    const text = event.clipboardData.getData("text/plain");
    if (text) onEvent({ type: "type", text });
    event.currentTarget.value = "";
  };

  return {
    inputRef,
    focusInput,
    inputProps: {
      onKeyDown,
      onKeyUp,
      onInput,
      onCompositionStart,
      onCompositionEnd,
      onPaste,
      onBlur: releaseKeys,
    },
  };
}
