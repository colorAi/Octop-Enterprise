import { useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { type TerminalThemeDefinition } from "../terminalThemes";

export interface TerminalViewHandle {
  /** Write raw data to the terminal display */
  write: (data: string) => void;
  /** Reset the terminal buffer (clears viewport + scrollback) */
  reset: () => void;
  /** Focus the terminal input */
  focus: () => void;
  /** Re-fit xterm to the current container dimensions */
  fit: () => void;
}

interface TerminalViewProps {
  /** Called whenever the user types something in the terminal */
  onData: (data: string) => void;
  /** Called when the terminal is resized */
  onResize?: (cols: number, rows: number) => void;
  /** Ref to expose imperative API */
  terminalRef?: React.MutableRefObject<TerminalViewHandle | null>;
  /** Terminal colour scheme definition */
  themeDefinition: TerminalThemeDefinition;
  /** Called once xterm is fully initialised and the write handle is ready */
  onReady?: () => void;
}

export default function TerminalView({
  onData,
  onResize,
  terminalRef,
  themeDefinition,
  onReady,
}: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const xtermRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const onReadyRef = useRef(onReady);
  onReadyRef.current = onReady;
  // Keep refs to latest callbacks so the xterm listeners (registered once on
  // mount) always invoke the current version without needing to re-register.
  const onDataRef = useRef(onData);
  onDataRef.current = onData;
  const onResizeRef = useRef(onResize);
  onResizeRef.current = onResize;

  // Expose imperative API
  const writeToTerminal = useCallback((data: string) => {
    xtermRef.current?.write(data);
  }, []);

  const resetTerminal = useCallback(() => {
    xtermRef.current?.reset();
  }, []);

  const focusTerminal = useCallback(() => {
    xtermRef.current?.focus();
  }, []);

  const fitTerminal = useCallback(() => {
    try {
      fitAddonRef.current?.fit();
      if (xtermRef.current) {
        onResizeRef.current?.(xtermRef.current.cols, xtermRef.current.rows);
      }
    } catch {
      // Ignore fit errors during unmount
    }
  }, []);

  useEffect(() => {
    if (terminalRef) {
      terminalRef.current = {
        write: writeToTerminal,
        reset: resetTerminal,
        focus: focusTerminal,
        fit: fitTerminal,
      };
    }
  }, [terminalRef, writeToTerminal, resetTerminal, focusTerminal, fitTerminal]);

  // Create xterm instance once
  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      fontFamily:
        '"JetBrains Mono", "Fira Code", "Cascadia Code", "Consolas", "Courier New", monospace',
      fontSize: 14,
      lineHeight: 1.4,
      cursorBlink: true,
      cursorStyle: "block",
      allowTransparency: false,
      scrollback: 5000,
      // PTY output often uses LF-only newlines; treat them like CRLF so the
      // cursor returns to column 0 and zsh prompt redraw does not leave a
      // spacer line of trailing spaces before the next prompt.
      convertEol: true,
      theme: themeDefinition.theme,
    });

    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();

    term.loadAddon(fitAddon);
    term.loadAddon(webLinksAddon);
    term.open(containerRef.current);
    fitAddon.fit();

    xtermRef.current = term;
    fitAddonRef.current = fitAddon;

    // Send initial size so the backend PTY matches the frontend cols/rows
    // from the very first keystroke — prevents long-command wrapping issues.
    onResizeRef.current?.(term.cols, term.rows);

    // Prevent the browser from intercepting keys that must reach the terminal.
    // Without this, keys like Ctrl+W close the tab, F5 refreshes the page, etc.
    term.attachCustomKeyEventHandler((ev: KeyboardEvent) => {
      // Let xterm handle all keydown/keyup events; never let them bubble.
      if (ev.type === "keydown" || ev.type === "keyup") {
        // Allow Ctrl+Shift+C / Ctrl+Shift+V for browser copy/paste.
        if (ev.ctrlKey && ev.shiftKey && (ev.key === "C" || ev.key === "V")) {
          return true;
        }
        // Suppress everything else so the browser doesn't intercept Ctrl+W,
        // F5, etc. while the terminal has focus.
        ev.stopPropagation();
      }
      return true; // still pass the event to xterm's own handler
    });

    // Forward keystrokes to parent via ref — always calls the latest onData
    const dataDisposable = term.onData((data) => {
      onDataRef.current(data);
    });

    // Handle resize via ref
    const resizeDisposable = term.onResize(({ cols, rows }) => {
      onResizeRef.current?.(cols, rows);
    });

    // Notify parent that xterm is ready — safe to call write() now.
    // Must be called after onData is registered so the WS connect path
    // that might immediately write output finds a fully wired-up terminal.
    onReadyRef.current?.();

    // ResizeObserver to auto-fit when container size changes.
    // Guard against zero-dimension callbacks that fire when the parent
    // container transitions to display:none — fitting with zero height
    // would shrink the terminal to one row and corrupt the layout.
    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      const { width, height } = entry.contentRect;
      if (width === 0 || height === 0) return;
      try {
        fitAddon.fit();
        // Sync the updated cols/rows to the backend PTY
        onResizeRef.current?.(term.cols, term.rows);
      } catch {
        // Ignore fit errors during unmount
      }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      dataDisposable.dispose();
      resizeDisposable.dispose();
      term.dispose();
      xtermRef.current = null;
      fitAddonRef.current = null;
    };
    // We intentionally only create the terminal once. Theme changes are
    // applied via the separate effect below so the terminal instance (and
    // its scrollback buffer) is preserved.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Hot-swap theme without destroying the terminal instance
  useEffect(() => {
    const term = xtermRef.current;
    if (!term) return;
    term.options.theme = themeDefinition.theme;
  }, [themeDefinition]);

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: "100%",
        overflow: "hidden",
        // Ensure xterm canvas fills the container
        display: "flex",
        flexDirection: "column",
      }}
    />
  );
}
