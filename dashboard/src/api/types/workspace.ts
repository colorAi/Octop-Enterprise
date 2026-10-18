export interface MdFileInfo {
  filename: string;
  path: string;
  size: number;
  created_time: string;
  modified_time: string;
}

export interface MdFileContent {
  content: string;
}

export interface MarkdownFile extends MdFileInfo {
  updated_at: number;
}

export interface DailyMemoryFile extends MdFileInfo {
  date: string;
  updated_at: number;
}

export interface FileTreeNode {
  name: string;
  path: string;
  is_dir: boolean;
  size: number;
  modified_time: string;
  children: FileTreeNode[];
}

export interface WorkspaceFileContent {
  name: string;
  path: string;
  content: string;
  size: number;
}

export interface SyncFromOpenClawRequest {
  openclaw_json_path?: string;
  sync_md?: boolean;
  sync_config?: boolean;
  sync_models?: boolean;
  sync_channels?: boolean;
  dry_run?: boolean;
}

export interface SyncFromOpenClawResponse {
  success: boolean;
  md_synced: number;
  md_skipped: number;
  skills_synced?: number;
  config_updated: boolean;
  updated_fields?: string[];
  warnings?: string[];
  errors?: string[];
}
