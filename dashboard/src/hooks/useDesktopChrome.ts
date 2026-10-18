import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  applyDesktopChrome,
  installDesktopWindowDrag,
  isDesktopShell,
  resolveDesktopChromeStyle,
  type DesktopChromeStyle,
} from "../utils/desktopChrome";

const DesktopChromeContext = createContext<DesktopChromeStyle | null>(null);

/** Activate frameless window chrome after the Wails bridge appears. */
export function useDesktopChrome(): DesktopChromeStyle | null {
  const [style, setStyle] = useState<DesktopChromeStyle | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tryApply = () => {
      if (cancelled || !isDesktopShell()) return false;
      const next = resolveDesktopChromeStyle();
      applyDesktopChrome(next);
      installDesktopWindowDrag();
      setStyle(next);
      return true;
    };
    window.addEventListener("octop:native-titlebar-ready", tryApply);
    const applied = tryApply();
    const timer = window.setInterval(() => {
      if (tryApply()) window.clearInterval(timer);
    }, 250);
    if (applied) window.clearInterval(timer);
    const stop = window.setTimeout(() => window.clearInterval(timer), 12_000);
    return () => {
      cancelled = true;
      window.removeEventListener("octop:native-titlebar-ready", tryApply);
      window.clearInterval(timer);
      window.clearTimeout(stop);
    };
  }, []);

  return style;
}

export function DesktopChromeProvider({
  value,
  children,
}: {
  value: DesktopChromeStyle | null;
  children: ReactNode;
}) {
  return createElement(DesktopChromeContext.Provider, { value }, children);
}

export function useDesktopChromeStyle(): DesktopChromeStyle | null {
  return useContext(DesktopChromeContext);
}
