// dashboard/src/pages/Agent/Skills/components/SectionHeader.tsx
import type { ReactNode } from "react";
import styles from "../index.module.less";

interface SectionHeaderProps {
  icon?: ReactNode;
  title: string;
  description?: string;
  count?: number;
}

/** Unified section header (optional icon + title + description + count). */
export function SectionHeader({
  icon,
  title,
  description,
  count,
}: SectionHeaderProps) {
  return (
    <div className={styles.sectionHead}>
      {icon && <span className={styles.sectionHeadIcon}>{icon}</span>}
      <div className={styles.sectionHeadText}>
        <div className={styles.sectionHeadTitleRow}>
          <span className={styles.sectionHeadTitle}>{title}</span>
          {typeof count === "number" && (
            <span className={styles.sectionCount}>{count}</span>
          )}
        </div>
        {description ? (
          <div className={styles.sectionHeadDesc}>{description}</div>
        ) : null}
      </div>
    </div>
  );
}
