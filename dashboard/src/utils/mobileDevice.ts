/** Phone/tablet UA — do not use viewport width (iPad landscape ≠ desktop for audio). */
export function isMobileUserAgent(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  if (/Android|iPhone|iPod/i.test(ua)) return true;
  if (/iPad/i.test(ua)) return true;
  // iPadOS 13+ reports MacIntel
  return navigator.maxTouchPoints > 1 && /MacIntel/.test(navigator.platform);
}
