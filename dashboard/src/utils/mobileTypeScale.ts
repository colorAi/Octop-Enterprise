/** Extra px applied to typography / icons when `useIsMobile()` is true. */
export const MOBILE_FONT_BUMP = 1;
export const MOBILE_ICON_BUMP = 2;

export function typeSize(desktop: number, isMobile?: boolean): number {
  return isMobile ? desktop + MOBILE_FONT_BUMP : desktop;
}

export function iconSize(desktop: number, isMobile?: boolean): number {
  return isMobile ? desktop + MOBILE_ICON_BUMP : desktop;
}
