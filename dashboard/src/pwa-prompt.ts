/**
 * Capture beforeinstallprompt synchronously at module load time.
 *
 * IMPORTANT: This file must be imported as early as possible (top of main.tsx)
 * because Chrome fires beforeinstallprompt right after the SW activates and
 * the page load event — which can happen BEFORE React mounts and runs useEffect.
 * If we register the listener inside a component useEffect, we always miss it.
 *
 * Pattern: capture here → store in module-level variable → React component reads it.
 */

export interface BeforeInstallPromptEvent extends Event {
  prompt(): Promise<void>;
  readonly userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

/** Stored prompt event captured before React mounts. */
let _deferredPrompt: BeforeInstallPromptEvent | null = null;
let _installed = false;
/** True once a service worker controls (or has activated on) this origin. */
let _swReady = false;

export interface PwaInstallSnapshot {
  prompt: BeforeInstallPromptEvent | null;
  swReady: boolean;
  installed: boolean;
}

let _installSnapshot: PwaInstallSnapshot = {
  prompt: null,
  swReady: false,
  installed: false,
};

// Listeners to notify React components when the state changes.
const _listeners = new Set<() => void>();

function _syncInstallSnapshot(): void {
  const prompt = _installed ? null : _deferredPrompt;
  if (
    _installSnapshot.prompt === prompt &&
    _installSnapshot.swReady === _swReady &&
    _installSnapshot.installed === _installed
  ) {
    return;
  }
  _installSnapshot = { prompt, swReady: _swReady, installed: _installed };
}

function _notify() {
  _syncInstallSnapshot();
  _listeners.forEach((fn) => fn());
}

function _refreshSwReady(): void {
  if (!("serviceWorker" in navigator)) return;
  void navigator.serviceWorker.getRegistration("/").then((reg) => {
    const ready = !!(reg?.active || navigator.serviceWorker.controller);
    if (ready !== _swReady) {
      _swReady = ready;
      _notify();
    }
  });
}

// Capture the event synchronously as early as possible.
if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    _deferredPrompt = e as BeforeInstallPromptEvent;
    _notify();
  });

  window.addEventListener("appinstalled", () => {
    _installed = true;
    _deferredPrompt = null;
    _notify();
  });

  window.addEventListener("load", () => {
    _refreshSwReady();
    navigator.serviceWorker?.addEventListener(
      "controllerchange",
      _refreshSwReady,
    );
  });

  // SW may already control the page on repeat visits before load fires.
  _refreshSwReady();
}

/** Subscribe to prompt state changes (for useSyncExternalStore). */
export function subscribePwaPrompt(fn: () => void): () => void {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

/** Snapshot for useSyncExternalStore (legacy — prompt only). */
export function getPwaPromptSnapshot(): BeforeInstallPromptEvent | null {
  if (_installed) return null;
  return _deferredPrompt;
}

/** Broader install UI snapshot (prompt + SW readiness). */
export function getPwaInstallSnapshot(): PwaInstallSnapshot {
  return _installSnapshot;
}

/** Trigger the native install dialog. */
export async function triggerInstall(): Promise<
  "accepted" | "dismissed" | "unavailable"
> {
  if (!_deferredPrompt) return "unavailable";
  await _deferredPrompt.prompt();
  const { outcome } = await _deferredPrompt.userChoice;
  _deferredPrompt = null;
  _notify();
  return outcome;
}

/** Wait until Chrome fires beforeinstallprompt (or timeout). */
export function waitForInstallPrompt(
  timeoutMs = 4000,
): Promise<BeforeInstallPromptEvent | null> {
  if (_deferredPrompt) return Promise.resolve(_deferredPrompt);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (prompt: BeforeInstallPromptEvent | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      unsub();
      resolve(prompt);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    const unsub = subscribePwaPrompt(() => {
      if (_deferredPrompt) finish(_deferredPrompt);
    });
  });
}
