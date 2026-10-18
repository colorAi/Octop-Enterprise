/** getRandomValues also works on LAN HTTP deployments. */
export function newClientMessageId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (n) => n.toString(16).padStart(2, "0")).join("");
}
