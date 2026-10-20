const compactTokens = new Intl.NumberFormat("en", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Stable K/M/B units for token counters in either UI language. */
export function formatTokenCount(tokens: number): string {
  return compactTokens.format(tokens);
}
