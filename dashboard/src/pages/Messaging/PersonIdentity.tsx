import type { CommunicationUser } from "../../api/types/communication";
import { useTranslation } from "react-i18next";
import { ProfileAvatar } from "../Admin/Users/ProfileAvatar";
import styles from "./PersonIdentity.module.less";

export function PersonAvatar({
  person,
  size = 32,
  online,
}: {
  person?: CommunicationUser | null;
  size?: number;
  online?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <span
      className={styles.avatar}
      style={{ width: size, height: size }}
      role="img"
      aria-label={person?.nickname}
    >
      <ProfileAvatar
        url={person?.avatar_url}
        icon={person?.avatar_icon}
        className={styles.portrait}
      />
      {online !== undefined && (
        <span
          className={`${styles.presence} ${
            online ? styles.online : styles.offline
          }`}
          role="img"
          aria-label={t(
            `communication.presence.${online ? "online" : "offline"}`,
          )}
          title={t(`communication.presence.${online ? "online" : "offline"}`)}
        />
      )}
    </span>
  );
}

export function PersonJobTitle({
  person,
}: {
  person?: CommunicationUser | null;
}) {
  return person?.job_title ? (
    <span className={styles.jobTitle} title={person.job_title}>
      {person.job_title}
    </span>
  ) : null;
}
