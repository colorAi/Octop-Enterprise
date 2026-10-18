import type { CommunicationUser } from "../../api/types/communication";
import { ProfileAvatar } from "../Admin/Users/ProfileAvatar";
import styles from "./PersonIdentity.module.less";

export function PersonAvatar({
  person,
  size = 32,
}: {
  person?: CommunicationUser | null;
  size?: number;
}) {
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
