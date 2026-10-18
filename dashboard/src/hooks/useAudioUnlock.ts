import { isMobileUserAgent } from "../utils/mobileDevice";

let audioCtx: AudioContext | null = null;

/** Tiny silent MP3 — unlocks iOS HTMLAudioElement in the user-gesture stack. */
const SILENT_MP3 =
  "data:audio/mp3;base64,SUQzBAAAAAAAI1RTU0UAAAAPAAADTGF2ZjU4LjQ1LjEwMAAAAAAAAAAAAAAA//tUxAAAAAAAAAAAAAAAAAAAAAAASW5mbwAAAA8AAAADAAAGhgBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVWqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqr////////////////////////////////////////AAAAAExhdmM1OC41MQAAAAAAAAAAAAAAACQCgAAAAAAAAAaG9/pWyqkAAAAAAP/7UMQRA8AAAaQAAAAgAAA0gAAABExBTUUzLjEwMFExAAAABAAAABpAAAECAAA=";

/**
 * Play silent audio synchronously inside a click/tap handler so a later
 * `audio.src = blobUrl; audio.play()` succeeds on iOS Safari.
 */
export function primeAudioElement(audio: HTMLAudioElement): void {
  audio.src = SILENT_MP3;
  audio.volume = 0.001;
  void audio.play().catch(() => {});
}

/**
 * Ensure AudioContext is unlocked (desktop server TTS).
 * Do NOT call before browser speechSynthesis — it causes repeat/stutter.
 */
export function ensureAudioUnlocked(): void {
  if (isMobileUserAgent()) return;
  if (!audioCtx) {
    const Ctor =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext: typeof AudioContext })
        .webkitAudioContext;
    if (Ctor) audioCtx = new Ctor();
  }
  if (audioCtx?.state === "suspended") {
    audioCtx.resume().catch(() => {});
  }
}

export function isAutoplayBlockedError(err: unknown): boolean {
  if (!(err instanceof Error)) return false;
  return err.name === "NotAllowedError" || err.name === "AbortError";
}
