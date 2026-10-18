import type { ITheme } from "@xterm/xterm";

export interface TerminalThemeDefinition {
  /** Unique key stored in localStorage */
  key: string;
  /** Display name (i18n key under "terminal.themes.*") */
  labelKey: string;
  /** Whether this theme is designed for dark backgrounds */
  isDark: boolean;
  /** xterm.js ITheme object */
  theme: ITheme;
}

// ─── Dark Themes ──────────────────────────────────────────────────────────────

const vscodeDark: TerminalThemeDefinition = {
  key: "vscode-dark",
  labelKey: "terminal.themes.vscodeDark",
  isDark: true,
  theme: {
    background: "#1e1e1e",
    foreground: "#d4d4d4",
    cursor: "#d4d4d4",
    cursorAccent: "#1e1e1e",
    selectionBackground: "rgba(255,255,255,0.2)",
    black: "#1e1e1e",
    red: "#f44747",
    green: "#4ec9b0",
    yellow: "#dcdcaa",
    blue: "#569cd6",
    magenta: "#c586c0",
    cyan: "#4fc1ff",
    white: "#d4d4d4",
    brightBlack: "#808080",
    brightRed: "#f44747",
    brightGreen: "#b5cea8",
    brightYellow: "#dcdcaa",
    brightBlue: "#9cdcfe",
    brightMagenta: "#c586c0",
    brightCyan: "#4fc1ff",
    brightWhite: "#ffffff",
  },
};

const dracula: TerminalThemeDefinition = {
  key: "dracula",
  labelKey: "terminal.themes.dracula",
  isDark: true,
  theme: {
    background: "#282a36",
    foreground: "#f8f8f2",
    cursor: "#f8f8f2",
    cursorAccent: "#282a36",
    selectionBackground: "rgba(68,71,90,0.6)",
    black: "#21222c",
    red: "#ff5555",
    green: "#50fa7b",
    yellow: "#f1fa8c",
    blue: "#bd93f9",
    magenta: "#ff79c6",
    cyan: "#8be9fd",
    white: "#f8f8f2",
    brightBlack: "#6272a4",
    brightRed: "#ff6e6e",
    brightGreen: "#69ff94",
    brightYellow: "#ffffa5",
    brightBlue: "#d6acff",
    brightMagenta: "#ff92df",
    brightCyan: "#a4ffff",
    brightWhite: "#ffffff",
  },
};

const oneDarkPro: TerminalThemeDefinition = {
  key: "one-dark-pro",
  labelKey: "terminal.themes.oneDarkPro",
  isDark: true,
  theme: {
    background: "#282c34",
    foreground: "#abb2bf",
    cursor: "#528bff",
    cursorAccent: "#282c34",
    selectionBackground: "rgba(62,68,81,0.6)",
    black: "#282c34",
    red: "#e06c75",
    green: "#98c379",
    yellow: "#e5c07b",
    blue: "#61afef",
    magenta: "#c678dd",
    cyan: "#56b6c2",
    white: "#abb2bf",
    brightBlack: "#5c6370",
    brightRed: "#e06c75",
    brightGreen: "#98c379",
    brightYellow: "#e5c07b",
    brightBlue: "#61afef",
    brightMagenta: "#c678dd",
    brightCyan: "#56b6c2",
    brightWhite: "#ffffff",
  },
};

const monokai: TerminalThemeDefinition = {
  key: "monokai",
  labelKey: "terminal.themes.monokai",
  isDark: true,
  theme: {
    background: "#272822",
    foreground: "#f8f8f2",
    cursor: "#f8f8f2",
    cursorAccent: "#272822",
    selectionBackground: "rgba(73,72,62,0.6)",
    black: "#272822",
    red: "#f92672",
    green: "#a6e22e",
    yellow: "#f4bf75",
    blue: "#66d9ef",
    magenta: "#ae81ff",
    cyan: "#a1efe4",
    white: "#f8f8f2",
    brightBlack: "#75715e",
    brightRed: "#f92672",
    brightGreen: "#a6e22e",
    brightYellow: "#f4bf75",
    brightBlue: "#66d9ef",
    brightMagenta: "#ae81ff",
    brightCyan: "#a1efe4",
    brightWhite: "#f9f8f5",
  },
};

const solarizedDark: TerminalThemeDefinition = {
  key: "solarized-dark",
  labelKey: "terminal.themes.solarizedDark",
  isDark: true,
  theme: {
    background: "#002b36",
    foreground: "#839496",
    cursor: "#839496",
    cursorAccent: "#002b36",
    selectionBackground: "rgba(7,54,66,0.6)",
    black: "#073642",
    red: "#dc322f",
    green: "#859900",
    yellow: "#b58900",
    blue: "#268bd2",
    magenta: "#d33682",
    cyan: "#2aa198",
    white: "#eee8d5",
    brightBlack: "#586e75",
    brightRed: "#cb4b16",
    brightGreen: "#586e75",
    brightYellow: "#657b83",
    brightBlue: "#839496",
    brightMagenta: "#6c71c4",
    brightCyan: "#93a1a1",
    brightWhite: "#fdf6e3",
  },
};

const tokyoNight: TerminalThemeDefinition = {
  key: "tokyo-night",
  labelKey: "terminal.themes.tokyoNight",
  isDark: true,
  theme: {
    background: "#1a1b26",
    foreground: "#c0caf5",
    cursor: "#c0caf5",
    cursorAccent: "#1a1b26",
    selectionBackground: "rgba(51,59,108,0.6)",
    black: "#15161e",
    red: "#f7768e",
    green: "#9ece6a",
    yellow: "#e0af68",
    blue: "#7aa2f7",
    magenta: "#bb9af7",
    cyan: "#7dcfff",
    white: "#a9b1d6",
    brightBlack: "#414868",
    brightRed: "#f7768e",
    brightGreen: "#9ece6a",
    brightYellow: "#e0af68",
    brightBlue: "#7aa2f7",
    brightMagenta: "#bb9af7",
    brightCyan: "#7dcfff",
    brightWhite: "#c0caf5",
  },
};

// ─── Light Themes ─────────────────────────────────────────────────────────────

const vscodeLight: TerminalThemeDefinition = {
  key: "vscode-light",
  labelKey: "terminal.themes.vscodeLight",
  isDark: false,
  theme: {
    background: "#ffffff",
    foreground: "#1e1e1e",
    cursor: "#1e1e1e",
    cursorAccent: "#ffffff",
    selectionBackground: "rgba(0,0,0,0.15)",
    black: "#000000",
    red: "#cd3131",
    green: "#008000",
    yellow: "#795e26",
    blue: "#0451a5",
    magenta: "#af00db",
    cyan: "#0070c1",
    white: "#808080",
    brightBlack: "#666666",
    brightRed: "#f44747",
    brightGreen: "#00bc8c",
    brightYellow: "#b89500",
    brightBlue: "#1e88e5",
    brightMagenta: "#ae67fa",
    brightCyan: "#1a8fff",
    brightWhite: "#1e1e1e",
  },
};

const solarizedLight: TerminalThemeDefinition = {
  key: "solarized-light",
  labelKey: "terminal.themes.solarizedLight",
  isDark: false,
  theme: {
    background: "#fdf6e3",
    foreground: "#657b83",
    cursor: "#657b83",
    cursorAccent: "#fdf6e3",
    selectionBackground: "rgba(238,232,213,0.6)",
    black: "#073642",
    red: "#dc322f",
    green: "#859900",
    yellow: "#b58900",
    blue: "#268bd2",
    magenta: "#d33682",
    cyan: "#2aa198",
    white: "#eee8d5",
    brightBlack: "#002b36",
    brightRed: "#cb4b16",
    brightGreen: "#586e75",
    brightYellow: "#657b83",
    brightBlue: "#839496",
    brightMagenta: "#6c71c4",
    brightCyan: "#93a1a1",
    brightWhite: "#fdf6e3",
  },
};

// ─── Registry ─────────────────────────────────────────────────────────────────

/** All available terminal themes, ordered for the UI dropdown */
export const TERMINAL_THEMES: TerminalThemeDefinition[] = [
  vscodeDark,
  dracula,
  oneDarkPro,
  monokai,
  solarizedDark,
  tokyoNight,
  vscodeLight,
  solarizedLight,
];

/** Quick lookup by theme key */
export const TERMINAL_THEME_MAP: Record<string, TerminalThemeDefinition> =
  Object.fromEntries(TERMINAL_THEMES.map((t) => [t.key, t]));

/** Default theme keys per system appearance */
export const DEFAULT_DARK_THEME = "dracula";
export const DEFAULT_LIGHT_THEME = "vscode-light";

/**
 * The default theme for users who have never chosen one.
 * Terminal is dark-by-default regardless of system appearance — this matches
 * the convention of professional terminal emulators and SSH clients.
 */
export const DEFAULT_THEME = DEFAULT_DARK_THEME;

/** localStorage key for persisting terminal theme preference */
export const TERMINAL_THEME_STORAGE_KEY = "terminal-theme";

/**
 * Get the appropriate theme definition.
 *
 * If the user has explicitly selected a theme (stored in localStorage), that
 * choice is always honoured. Otherwise, we default to a dark theme to provide
 * a professional terminal experience similar to SSH clients.
 */
export function getTerminalTheme(
  themeKey: string | null,
  isDark: boolean,
): TerminalThemeDefinition {
  if (themeKey && TERMINAL_THEME_MAP[themeKey]) {
    return TERMINAL_THEME_MAP[themeKey];
  }
  // Dark-by-default for first-time users; once they pick a theme it persists.
  return TERMINAL_THEME_MAP[isDark ? DEFAULT_DARK_THEME : DEFAULT_LIGHT_THEME];
}
