export type Sequence = string;
export interface CommunicationUser {
  user_id: string;
  nickname: string;
  avatar_icon?: string;
  avatar_url?: string | null;
  department_id?: string | null;
  status?: "active" | "disabled" | "deleted";
  can_message?: boolean;
  is_admin?: boolean;
  job_title_id?: string | null;
  job_title?: string | null;
}
export interface JobTitle {
  job_title_id: string;
  name: string;
  builtin: boolean;
}
export interface Department {
  department_id: string;
  name: string;
  parent_id: string | null;
  sort_order: number;
  enabled: boolean;
  conversation_id: string | null;
  member_count?: number;
}
export interface MembershipEpoch {
  epoch: number;
  role: "owner" | "admin" | "member";
  visible_from_seq: Sequence;
  visible_through_seq: Sequence | null;
  left_at: string | null;
  mute_until: string | null;
}
export type GroupKind = "company" | "department" | "project";
export interface GroupInfo {
  group_kind?: GroupKind;
  is_member?: boolean;
  status?: string;
  member_count?: number;
  members_can_invite?: boolean;
  conversation_id?: string;
  name: string;
  announcement: string | null;
  owner_user_id: string | null;
  mute_all: boolean;
  my_role?: "owner" | "admin" | "member";
}
export interface GroupMember extends CommunicationUser {
  membership_source?: "automatic" | "invited";
  granted_by?: string | null;
  role?: "owner" | "admin" | "member";
  my_nickname?: string;
  mute_until?: string | null;
}
export interface ManagedGroup extends GroupInfo {
  members: GroupMember[];
}
export interface Conversation {
  conversation_id: string;
  type: "private" | "group";
  peer_user_id?: string | null;
  last_seq: Sequence;
  last_read_seq?: Sequence;
  cleared_through_seq?: Sequence;
  hidden_at_seq?: Sequence | null;
  pinned: boolean;
  muted: boolean;
  unread?: number;
  archived?: boolean;
  can_send?: boolean;
  status?: string;
  updated_at?: string;
  group?: GroupInfo | null;
  membership_epochs?: MembershipEpoch[];
  last_message?: { type: string; preview: string };
}
export interface CommunicationAttachment {
  asset_id: string;
  state: string;
  purpose?: "chat_image" | "chat_file";
  filename?: string;
  mime?: string;
  size_bytes?: number;
}
export interface CommunicationMessage {
  message_id: string;
  seq: Sequence;
  sender_id: string;
  type: "text" | "image" | "file" | "system";
  text: string | null;
  recalled: boolean;
  created_at: string;
  client_message_id?: string;
  attachments?: CommunicationAttachment[];
  system_kind?: string;
  reply_to_message_id?: string;
  reply_preview?: CommunicationMessage | null;
  mention_user_ids?: string[];
  mention_all?: boolean;
}
export interface SendMessage {
  client_message_id: string;
  text?: string;
  asset_ids?: string[];
  reply_to_message_id?: string;
  mention_user_ids?: string[];
  mention_all?: boolean;
}
export interface SendReceipt {
  message_id: string;
  seq: Sequence;
  created_at: string;
  conversation_id: string;
  replayed?: boolean;
}
export interface SyncEvent {
  seq: Sequence;
  type: string;
  object_id: string;
  version: Sequence;
  payload: Record<string, unknown>;
}
export interface Snapshot {
  snapshot_id: string;
  until_seq: Sequence;
  cursor: string;
  chunk_count: number;
}
export interface SnapshotChunk {
  snapshot_id: string;
  chunk_index: number;
  chunk_count: number;
  events: SyncEvent[];
}
export interface SyncPage {
  events: SyncEvent[];
  cursor: string;
  has_more: boolean;
  head_seq: Sequence;
}
export interface ConversationPage {
  items: Conversation[];
  has_more: boolean;
  next_cursor?: string;
}
export interface MessagePage {
  items: CommunicationMessage[];
  has_more: boolean;
  next_before_seq: Sequence;
}
export interface ImageUpload {
  upload_id: string;
  asset_id: string;
  state?: string;
}
export interface ImageUploadStatus extends ImageUpload {
  state: string;
  last_error: string;
}
