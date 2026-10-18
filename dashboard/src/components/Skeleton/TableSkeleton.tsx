import { Skeleton } from "antd";

interface TableSkeletonProps {
  rows?: number;
  columns?: number;
}

/**
 * Displays a skeleton placeholder that mimics a table while data is loading.
 * Uses lightweight divs + Ant Design Skeleton to avoid importing the full Table.
 */
export function TableSkeleton({ rows = 5, columns = 4 }: TableSkeletonProps) {
  return (
    <div style={{ width: "100%", overflow: "hidden" }}>
      {/* Header row */}
      <div
        style={{
          display: "flex",
          gap: 16,
          padding: "12px 16px",
          borderBottom: "1px solid var(--fn-border-color, #f0f0f0)",
          background: "var(--fn-bg-secondary, #fafafa)",
        }}
      >
        {Array.from({ length: columns }, (_, i) => (
          <Skeleton.Input
            key={i}
            active
            size="small"
            style={{
              width: i === 0 ? 100 : 80,
              flex: i === columns - 1 ? 1 : undefined,
            }}
          />
        ))}
      </div>
      {/* Data rows */}
      {Array.from({ length: rows }, (_, rowIdx) => (
        <div
          key={rowIdx}
          style={{
            display: "flex",
            gap: 16,
            padding: "14px 16px",
            borderBottom: "1px solid var(--fn-border-color, #f0f0f0)",
            alignItems: "center",
          }}
        >
          {Array.from({ length: columns }, (_, colIdx) => (
            <Skeleton.Input
              key={colIdx}
              active
              size="small"
              style={{
                width: colIdx === 0 ? 120 : colIdx === columns - 1 ? 60 : 90,
                flex: colIdx === Math.floor(columns / 2) ? 1 : undefined,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
