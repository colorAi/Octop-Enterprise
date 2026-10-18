/** Stable across sliding renewals, different for each login, even for the same user. */
export function tokenSessionIdentity(token: string): string {
  if (!token) return "";
  try {
    const claims = JSON.parse(
      atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")),
    );
    return `${claims.sub}:${claims.sid ?? token}`;
  } catch {
    return token;
  }
}
