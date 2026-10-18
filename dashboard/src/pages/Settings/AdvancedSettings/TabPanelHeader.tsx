import type { ReactNode } from "react";
import tabStyles from "./tabContent.module.less";

interface TabPanelHeaderProps {
  icon: ReactNode;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}

export function TabPanelHeader({
  icon,
  title,
  description,
  actions,
}: TabPanelHeaderProps) {
  return (
    <div className={tabStyles.panelHeader}>
      <span className={tabStyles.panelHeaderIcon} aria-hidden="true">
        {icon}
      </span>
      <div className={tabStyles.panelHeaderBody}>
        <div className={tabStyles.panelHeaderRow}>
          <h3 className={tabStyles.sectionTitle}>{title}</h3>
          {actions ? (
            <div className={tabStyles.panelHeaderActions}>{actions}</div>
          ) : null}
        </div>
        {description ? (
          <div className={tabStyles.sectionDesc}>{description}</div>
        ) : null}
      </div>
    </div>
  );
}
