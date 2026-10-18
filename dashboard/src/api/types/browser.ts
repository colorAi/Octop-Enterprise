// Browser session API types

export interface BrowserSession {
  session_id: string;
  profile_name: string;
  conversation_id: string;
  channel_source: string;
  state: string;
  control_owner: "agent" | "user";
  current_url: string;
  created_at: number;
  last_activity_at: number;
}

export type DisplayEnvironment = "desktop" | "headless-server";

export interface BrowserSessionsResponse {
  ok: boolean;
  environment: DisplayEnvironment;
  sessions: BrowserSession[];
}

export interface BrowserRecordReplayActive {
  recordingId: string;
  profile?: string;
  startUrl?: string;
  events?: number;
}

export interface BrowserRecordReplayStatus {
  ok: boolean;
  active: BrowserRecordReplayActive | null;
  latestRecordingId?: string | null;
  error?: string;
}

export interface BrowserRecordStartRequest {
  profile?: string;
  agentProfile?: string;
  name?: string;
  privacy?: "none" | "mask-sensitive" | "mask-all";
}

export interface BrowserRecordStartResponse {
  ok: boolean;
  recordingId?: string;
  daemon?: boolean;
  store?: string;
  startUrl?: string;
  name?: string;
  error?: string;
}

export interface BrowserRecordStopRequest {
  recordingId?: string | null;
  name?: string;
  generateSteps?: boolean;
}

export interface BrowserRecordStopResponse {
  ok: boolean;
  recordingId?: string;
  events?: number;
  steps?: number;
  stepsPath?: string;
  skillDraft?: string;
  error?: string;
}

export interface BrowserReplayRequest {
  recordingId: string;
  profile?: string;
  inputs?: Record<string, string>;
}

export interface BrowserReplayResponse {
  status: "passed" | "failed" | string;
  recordingId?: string;
  reportPath?: string;
  error?: string;
  steps?: Array<Record<string, unknown>>;
}

export interface BrowserRecordStopAndGenerateSkillRequest {
  recordingId?: string | null;
  name?: string;
  generateSteps?: boolean;
}

export interface BrowserRecordStopAndGenerateSkillResponse {
  ok: boolean;
  recordingId?: string;
  events?: number;
  steps?: number;
  stepsPath?: string;
  skillContent?: string | null;
  skillName?: string | null;
  error?: string;
}

export interface BrowserSkillContentRequest {
  recordingId: string;
}

export interface BrowserSkillContentResponse {
  ok: boolean;
  recordingId: string;
  skillContent?: string | null;
  skillName?: string | null;
  skillExists: boolean;
}
