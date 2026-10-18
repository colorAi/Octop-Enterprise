import { describe, it, expect } from "vitest";
import {
  isSensitiveKey,
  maskSensitiveValue,
  getDisplayValue,
} from "./sensitive";

describe("Sensitive environment variable detection", () => {
  describe("isSensitiveKey", () => {
    it("should detect KEY pattern", () => {
      expect(isSensitiveKey("OPENAI_API_KEY")).toBe(true);
      expect(isSensitiveKey("AWS_ACCESS_KEY_ID")).toBe(true);
      expect(isSensitiveKey("DATABASE_KEY")).toBe(true);
    });

    it("should detect SECRET pattern", () => {
      expect(isSensitiveKey("AWS_SECRET_ACCESS_KEY")).toBe(true);
      expect(isSensitiveKey("CLIENT_SECRET")).toBe(true);
      expect(isSensitiveKey("API_SECRET")).toBe(true);
    });

    it("should detect TOKEN pattern", () => {
      expect(isSensitiveKey("AUTH_TOKEN")).toBe(true);
      expect(isSensitiveKey("REFRESH_TOKEN")).toBe(true);
      expect(isSensitiveKey("GITHUB_TOKEN")).toBe(true);
    });

    it("should detect PASSWORD pattern", () => {
      expect(isSensitiveKey("DB_PASSWORD")).toBe(true);
      expect(isSensitiveKey("ADMIN_PASSWORD")).toBe(true);
      expect(isSensitiveKey("DATABASE_PASSWORD")).toBe(true);
    });

    it("should detect other sensitive patterns", () => {
      expect(isSensitiveKey("OPENAI_API_KEY")).toBe(true);
      expect(isSensitiveKey("ANTHROPIC_API_KEY")).toBe(true);
      expect(isSensitiveKey("AWS_ACCESS_KEY")).toBe(true);
      expect(isSensitiveKey("JWT_SECRET")).toBe(true);
      expect(isSensitiveKey("PRIVATE_KEY")).toBe(true);
      expect(isSensitiveKey("GITLAB_TOKEN")).toBe(true);
    });

    it("should be case insensitive", () => {
      expect(isSensitiveKey("openai_api_key")).toBe(true);
      expect(isSensitiveKey("OpenAI_API_KEY")).toBe(true);
      expect(isSensitiveKey("db_password")).toBe(true);
    });

    it("should not match non-sensitive keys", () => {
      expect(isSensitiveKey("DEBUG_MODE")).toBe(false);
      expect(isSensitiveKey("LOG_LEVEL")).toBe(false);
      expect(isSensitiveKey("APP_NAME")).toBe(false);
      expect(isSensitiveKey("CACHE_SIZE")).toBe(false);
    });
  });

  describe("maskSensitiveValue", () => {
    it("should return empty string for empty value", () => {
      expect(maskSensitiveValue("")).toBe("");
    });

    it("should mask short values", () => {
      expect(maskSensitiveValue("123")).toBe("***");
      expect(maskSensitiveValue("pass")).toBe("****");
    });

    it("should show first 2 and last 2 characters", () => {
      const masked = maskSensitiveValue("sk-123456789abckey");
      expect(masked).toMatch(/^sk.*ey$/);
      expect(masked).toContain("*");
      expect(masked.length).toBeLessThanOrEqual(20);
    });

    it("should handle various lengths", () => {
      const short = maskSensitiveValue("abcde");
      expect(short).toMatch(/^ab.*de$/);

      const long = maskSensitiveValue("verylongsecretkeyvalue");
      expect(long).toMatch(/^ve.*ue$/);
    });
  });

  describe("getDisplayValue", () => {
    it("should return original value if key is not sensitive", () => {
      expect(getDisplayValue("DEBUG_MODE", "true", false)).toBe("true");
      expect(getDisplayValue("LOG_LEVEL", "DEBUG", true)).toBe("DEBUG");
    });

    it("should return original value if isVisible is true", () => {
      expect(getDisplayValue("API_KEY", "sk-123456", true)).toBe("sk-123456");
      expect(getDisplayValue("DB_PASSWORD", "secret", true)).toBe("secret");
    });

    it("should return masked value if sensitive and not visible", () => {
      const display = getDisplayValue("API_KEY", "sk-123456", false);
      expect(display).not.toBe("sk-123456");
      expect(display).toContain("*");
    });

    it("should handle combination of conditions", () => {
      // Sensitive + invisible = masked
      expect(getDisplayValue("AUTH_TOKEN", "abc123", false)).toContain("*");

      // Sensitive + visible = original
      expect(getDisplayValue("AUTH_TOKEN", "abc123", true)).toBe("abc123");

      // Not sensitive + invisible = original (safety, no sensitive info anyway)
      expect(getDisplayValue("DEBUG", "true", false)).toBe("true");
    });
  });
});
