import { Skeleton } from "antd";

interface ListSkeletonProps {
  rows?: number;
}

/**
 * Displays a skeleton placeholder that mimics a vertical list while data is loading.
 * Suitable for key-value pair lists (e.g. Environments page).
 */
export function ListSkeleton({ rows = 4 }: ListSkeletonProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "8px 0",
            borderBottom: "1px solid var(--fn-border-color, #f0f0f0)",
          }}
        >
          <Skeleton.Input active size="small" style={{ width: 140 }} />
          <Skeleton.Input active size="small" style={{ flex: 1 }} />
          <Skeleton.Button active size="small" />
        </div>
      ))}
    </div>
  );
}
