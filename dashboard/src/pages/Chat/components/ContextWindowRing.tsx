import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Popover, Spin, Drawer } from "antd";
import {
  octopThreadsApi,
  type ContextUsageBreakdown,
  type ContextUsageSegmentKey,
} from "../../../api/modules/octopThreads";
import { isPendingThread } from "../hooks/useSessions";
import { contextUsedPercent } from "../hooks/useChatContextWindow";
import { formatTokenCount } from "../../../utils/formatTokenCount";
import styles from "../index.module.less";

const DEFAULT_MAX = 128_000;
const BREAKDOWN_CACHE_TTL_MS = 30_000;
const EMPTY_CONNECTORS: string[] = [];

const SEGMENT_COLORS: Record<ContextUsageSegmentKey, string> = {
  system_prompt: "#9ca3af",
  tool_definitions: "#c4b5fd",
  rules: "#86efac",
  skills: "#fbbf24",
  mcp: "#e879f9",
  subagent_definitions: "#3b82f6",
  conversation: "#22d3ee",
  unclassified: "#9ca3af",
};

interface ContextWindowRingProps {
  usedTokens: number | null;
  maxTokens: number;
  agentId?: string | null;
  threadId?: string | null;
  selectedConnectors?: string[];
  isMobile?: boolean;
  liveBreakdown?: ContextUsageBreakdown | null;
}

export default function ContextWindowRing({
  usedTokens,
  maxTokens,
  agentId,
  threadId,
  selectedConnectors = EMPTY_CONNECTORS,
  isMobile = false,
  liveBreakdown = null,
}: ContextWindowRingProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [breakdown, setBreakdown] = useState<ContextUsageBreakdown | null>(
    null,
  );
  const cacheRef = useRef<{
    key: string;
    at: number;
    data: ContextUsageBreakdown;
  } | null>(null);
  // Stream hint — kept in a ref so prefetch does not re-fire on every token tick.
  const hintUsedRef = useRef(usedTokens ?? 0);
  hintUsedRef.current = usedTokens ?? 0;

  const max = maxTokens > 0 ? maxTokens : DEFAULT_MAX;
  const hintUsed = usedTokens ?? 0;

  const cacheKey = useMemo(
    () =>
      [
        agentId ?? "",
        threadId ?? "",
        String(max),
        selectedConnectors.join(","),
      ].join("|"),
    [agentId, threadId, max, selectedConnectors],
  );
  const requestKeyRef = useRef(cacheKey);
  requestKeyRef.current = cacheKey;
  const requestSequenceRef = useRef(0);

  const loadBreakdown = useCallback(
    async (opts?: { silent?: boolean; force?: boolean }) => {
      if (!agentId || !threadId || isPendingThread(threadId)) return;
      const cached = cacheRef.current;
      const hint = hintUsedRef.current;
      if (
        !opts?.force &&
        cached &&
        cached.key === cacheKey &&
        (hint <= 0 ||
          cached.data.used_tokens === Math.min(hint, cached.data.max_tokens)) &&
        Date.now() - cached.at < BREAKDOWN_CACHE_TTL_MS
      ) {
        setBreakdown(cached.data);
        return;
      }
      const sequence = ++requestSequenceRef.current;
      if (!opts?.silent) setLoading(true);
      try {
        // Prefer a pure harness snapshot. Pass stream hint only as a fallback
        // when we do not already have a segmented breakdown cached.
        const haveSegments =
          cached?.key === cacheKey &&
          (cached.data.segments?.length ?? 0) > 0 &&
          cached.data.used_tokens > 0 &&
          (hint <= 0 ||
            cached.data.used_tokens === Math.min(hint, cached.data.max_tokens));
        const data = await octopThreadsApi.contextUsage(agentId, threadId, {
          maxTokens: max,
          inputTokens: !haveSegments && hint > 0 ? hint : undefined,
          mcpServers: selectedConnectors,
        });
        if (
          requestSequenceRef.current !== sequence ||
          requestKeyRef.current !== cacheKey ||
          hintUsedRef.current !== hint
        )
          return;
        if (data.used_tokens > 0) {
          cacheRef.current = { key: cacheKey, at: Date.now(), data };
          setBreakdown(data);
        } else {
          // Empty harness snapshot (old threads) must not pin the ring at 0%.
          cacheRef.current = null;
          setBreakdown(null);
        }
      } catch {
        if (
          !opts?.silent &&
          requestSequenceRef.current === sequence &&
          requestKeyRef.current === cacheKey &&
          hintUsedRef.current === hint
        )
          setBreakdown(null);
      } finally {
        if (
          !opts?.silent &&
          requestSequenceRef.current === sequence &&
          requestKeyRef.current === cacheKey &&
          hintUsedRef.current === hint
        )
          setLoading(false);
      }
    },
    [agentId, threadId, max, selectedConnectors, cacheKey],
  );

  // Reset detail cache on thread/filter changes. The ring itself keeps growing
  // from the live/persisted token hint and never needs a checkpoint read.
  useEffect(() => {
    setBreakdown(null);
    cacheRef.current = null;
  }, [cacheKey]);

  useEffect(() => {
    if (open && !liveBreakdown) void loadBreakdown();
  }, [open, liveBreakdown, usedTokens, loadBreakdown]);

  const matchingBreakdown =
    cacheRef.current?.key === cacheKey &&
    (hintUsed <= 0 ||
      breakdown?.used_tokens ===
        Math.min(hintUsed, breakdown?.max_tokens || max))
      ? breakdown
      : null;
  const activeBreakdown = liveBreakdown ?? matchingBreakdown;

  const ringUsed = useMemo(() => {
    if (hintUsed > 0) return Math.min(hintUsed, max);
    if (activeBreakdown && activeBreakdown.used_tokens > 0) {
      const breakdownMax =
        activeBreakdown.max_tokens > 0 ? activeBreakdown.max_tokens : max;
      return Math.min(activeBreakdown.used_tokens, breakdownMax);
    }
    // Until prefetch returns, show last-call hint (capped so a stale
    // turn-sum cannot flash a full ring).
    return 0;
  }, [activeBreakdown, hintUsed, max]);

  const ringMax =
    activeBreakdown && activeBreakdown.max_tokens > 0
      ? activeBreakdown.max_tokens
      : max;

  const { usedPct, strokeColor, dashOffset, circumference } = useMemo(() => {
    const usedRatio = ringMax > 0 ? Math.min(ringUsed / ringMax, 1) : 0;
    const usedPercent = contextUsedPercent(ringUsed, ringMax);
    const r = 13;
    const circ = 2 * Math.PI * r;
    let color = "var(--fn-color-success, #22c55e)";
    if (usedRatio >= 0.8) {
      color = "var(--fn-color-danger, #ef4444)";
    } else if (usedRatio >= 0.5) {
      color = "var(--fn-color-warning, #eab308)";
    }
    return {
      usedPct: usedPercent,
      strokeColor: color,
      dashOffset: circ * (1 - usedRatio),
      circumference: circ,
    };
  }, [ringUsed, ringMax]);

  const tooltip = t("chat.contextWindow.tooltip", {
    used: formatTokenCount(ringUsed),
    max: formatTokenCount(ringMax),
    percent: usedPct,
  });

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
  };

  const display = activeBreakdown ?? {
    max_tokens: ringMax,
    used_tokens: ringUsed,
    segments: [] as ContextUsageBreakdown["segments"],
  };
  const displayUsed = display.used_tokens > 0 ? display.used_tokens : ringUsed;
  const displayMax = display.max_tokens > 0 ? display.max_tokens : ringMax;
  const displayPct = contextUsedPercent(displayUsed, displayMax);
  const segments =
    display.segments.length > 0
      ? display.segments
      : displayUsed > 0
      ? ([
          { key: "unclassified", tokens: displayUsed },
        ] as ContextUsageBreakdown["segments"])
      : display.segments;
  const segmentTotal = segments.reduce((sum, item) => sum + item.tokens, 0);

  const popoverContent = (
    <div className={styles.contextUsagePanel}>
      <div className={styles.contextUsageTitle}>
        {t("chat.contextWindow.breakdownTitle")}
      </div>
      <div className={styles.contextUsageSubtitle}>
        {t("chat.contextWindow.breakdownPercent", { percent: displayPct })}
      </div>
      <div className={styles.contextUsageHint}>
        {t(
          segments.some((item) => item.key === "unclassified")
            ? "chat.contextWindow.breakdownUnavailableHint"
            : "chat.contextWindow.breakdownHint",
        )}
      </div>
      {loading && !liveBreakdown ? (
        <div className={styles.contextUsageLoading}>
          <Spin size="small" />
        </div>
      ) : (
        <>
          <div
            className={styles.contextUsageBar}
            role="img"
            aria-label={t("chat.contextWindow.breakdownTitle")}
          >
            {segmentTotal > 0 ? (
              segments.map((segment) => (
                <span
                  key={segment.key}
                  className={styles.contextUsageBarSegment}
                  style={{
                    // Provider input usage owns the total bar width. Segment
                    // estimates contribute only their relative composition.
                    flexGrow: (displayUsed * segment.tokens) / segmentTotal,
                    background: SEGMENT_COLORS[segment.key],
                  }}
                />
              ))
            ) : (
              <span
                className={styles.contextUsageBarSegment}
                style={{
                  flexGrow: displayUsed,
                  background: strokeColor,
                }}
              />
            )}
            <span
              className={styles.contextUsageBarRemainder}
              style={{ flexGrow: Math.max(displayMax - displayUsed, 0) }}
            />
          </div>
          <ul className={styles.contextUsageLegend}>
            {segments.map((segment) => (
              <li key={segment.key} className={styles.contextUsageLegendItem}>
                <span
                  className={styles.contextUsageLegendSwatch}
                  style={{ background: SEGMENT_COLORS[segment.key] }}
                />
                <span className={styles.contextUsageLegendLabel}>
                  {t(`chat.contextWindow.segments.${segment.key}`)}
                </span>
                <span className={styles.contextUsageLegendValue}>
                  ~{formatTokenCount(segment.tokens)}
                </span>
              </li>
            ))}
            {segments.length === 0 && (
              <li className={styles.contextUsageLegendItem}>
                <span className={styles.contextUsageLegendLabel}>
                  {tooltip}
                </span>
              </li>
            )}
          </ul>
        </>
      )}
    </div>
  );

  const ringInner = (
    <>
      <svg className={styles.contextRingSvg} viewBox="0 0 32 32" aria-hidden>
        <circle
          className={styles.contextRingTrack}
          cx="16"
          cy="16"
          r="13"
          fill="none"
          strokeWidth="3"
        />
        <circle
          className={styles.contextRingProgress}
          cx="16"
          cy="16"
          r="13"
          fill="none"
          strokeWidth="3"
          stroke={strokeColor}
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          strokeLinecap="round"
          transform="rotate(-90 16 16)"
        />
      </svg>
      <span className={styles.contextRingLabel}>{usedPct}</span>
    </>
  );

  const ring = (
    <div
      className={styles.contextRingBtn}
      role="progressbar"
      aria-valuenow={usedPct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={tooltip}
    >
      {ringInner}
    </div>
  );

  if (!agentId || !threadId) {
    return ring;
  }

  if (isMobile) {
    return (
      <>
        <button
          type="button"
          className={styles.contextRingBtn}
          onClick={() => handleOpenChange(true)}
          aria-label={tooltip}
        >
          {ringInner}
        </button>
        <Drawer
          open={open}
          onClose={() => handleOpenChange(false)}
          placement="bottom"
          height="auto"
          title={t("chat.contextWindow.breakdownTitle")}
          className={styles.mobilePickerDrawer}
          styles={{
            body: {
              padding: "12px 16px calc(16px + env(safe-area-inset-bottom))",
            },
          }}
          destroyOnHidden
        >
          {popoverContent}
        </Drawer>
      </>
    );
  }

  return (
    <Popover
      content={popoverContent}
      trigger="click"
      open={open}
      onOpenChange={handleOpenChange}
      placement="topRight"
      overlayClassName={styles.contextUsagePopover}
    >
      {ring}
    </Popover>
  );
}
