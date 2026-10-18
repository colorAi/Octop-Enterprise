import { Card, Skeleton } from "antd";

interface CardSkeletonProps {
  count?: number;
  /** CSS grid or flex layout style for the container */
  containerStyle?: React.CSSProperties;
}

/**
 * Displays skeleton placeholder cards while data is loading.
 * Useful for grid/card layouts (e.g. Channels page).
 */
export function CardSkeleton({ count = 4, containerStyle }: CardSkeletonProps) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
        gap: 16,
        ...containerStyle,
      }}
    >
      {Array.from({ length: count }, (_, i) => (
        <Card key={i} style={{ borderRadius: 8 }}>
          <Skeleton active avatar paragraph={{ rows: 2 }} />
        </Card>
      ))}
    </div>
  );
}
