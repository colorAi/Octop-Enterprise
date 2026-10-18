/**
 * Utilities for detecting and masking sensitive environment variable values.
 */

/** Pattern for sensitive environment variable keys. */
const SENSITIVE_PATTERNS = [
  /^.*_KEY$/i, // OPENAI_API_KEY, AWS_ACCESS_KEY_ID, etc.
  /^.*_SECRET$/i, // AWS_SECRET_ACCESS_KEY, CLIENT_SECRET, etc.
  /^.*_TOKEN$/i, // AUTH_TOKEN, REFRESH_TOKEN, API_TOKEN, etc.
  /^.*_PASSWORD$/i, // DB_PASSWORD, ADMIN_PASSWORD, etc.
  /^.*_CREDENTIAL$/i, // AWS_CREDENTIAL, SERVICE_CREDENTIAL, etc.
  /^.*_APIKEY$/i, // LANGFUSE_APIKEY, etc.
  /^OPENAI/i, // OPENAI_* (broader match)
  /^ANTHROPIC/i, // ANTHROPIC_API_KEY, etc.
  /^AWS_/i, // AWS_* credentials
  /^DATABASE.*PASSWORD$/i, // DATABASE_PASSWORD, etc.
  /^DB_/i, // DB_* connection strings
  /^JWT_/i, // JWT_SECRET, JWT_PRIVATE_KEY, etc.
  /^PRIVATE_KEY$/i, // PRIVATE_KEY, GPG_PRIVATE_KEY, etc.
  /^AUTH_SECRET$/i, // AUTH_SECRET, etc.
  /^GITHUB_TOKEN$/i, // GITHUB_TOKEN, etc.
  /^GITLAB_TOKEN$/i, // GITLAB_TOKEN, etc.
  /^BITBUCKET_TOKEN$/i, // BITBUCKET_TOKEN, etc.
];

/**
 * Check if an environment variable key is sensitive.
 * Returns true if the key matches any sensitive pattern.
 *
 * @param key - Environment variable key name
 * @returns true if key is sensitive, false otherwise
 */
export function isSensitiveKey(key: string): boolean {
  return SENSITIVE_PATTERNS.some((pattern) => pattern.test(key));
}

/**
 * Mask a sensitive value by replacing content with asterisks.
 * Shows only the first and last 2 characters, or all asterisks if too short.
 *
 * @param value - The original value to mask
 * @returns Masked value (e.g., "sk**...***key" for "sk-123456789abckey")
 */
export function maskSensitiveValue(value: string): string {
  if (!value) return "";

  // For very short values, just use asterisks
  if (value.length <= 4) {
    return "*".repeat(Math.min(value.length, 8));
  }

  // Show first 2 and last 2 characters, fill middle with asterisks
  const first = value.substring(0, 2);
  const last = value.substring(value.length - 2);
  const middle = "*".repeat(Math.max(value.length - 4, 4));

  return `${first}${middle}${last}`;
}

/**
 * Get display text for a value - either masked or original.
 *
 * @param key - Environment variable key
 * @param value - The value
 * @param isVisible - Whether to show the actual value
 * @returns Display text (masked or original)
 */
export function getDisplayValue(
  key: string,
  value: string,
  isVisible: boolean,
): string {
  if (isVisible || !isSensitiveKey(key)) {
    return value;
  }
  return maskSensitiveValue(value);
}
