import { useId, useState } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import styles from "./expertAssignments.module.less";

export default function ExpertDescription({
  description,
}: {
  description?: string | null;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const id = useId();
  if (!description?.trim()) return null;

  return (
    <span className={styles.expertDescription}>
      <button
        type="button"
        className={styles.descriptionToggle}
        aria-expanded={expanded}
        aria-controls={id}
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setExpanded(!expanded);
        }}
      >
        {t(
          expanded
            ? "employeeResources.collapseDescription"
            : "employeeResources.expandDescription",
        )}
        <ChevronDown size={12} aria-hidden="true" />
      </button>
      <span id={id} className={styles.descriptionText} hidden={!expanded}>
        {description}
      </span>
    </span>
  );
}
