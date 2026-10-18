export interface MBTIDimensions {
  ei: [string, number];
  sn: [string, number];
  tf: [string, number];
  jp: [string, number];
}

export interface MBTIBehavior {
  answer_style: string;
  casual_chat: string;
  conflict: string;
  creativity: string;
  emotion: string;
  planning: string;
  answer_style_zh: string;
  casual_chat_zh: string;
  conflict_zh: string;
  creativity_zh: string;
  emotion_zh: string;
  planning_zh: string;
}

export interface MBTIType {
  code: string;
  name_zh: string;
  name_en: string;
  nickname_zh: string;
  summary_zh: string;
  summary_en: string;
  descriptors_zh: string;
  descriptors_en: string;
  dimensions: MBTIDimensions;
  behavior: MBTIBehavior;
  color: string;
  symbol: string;
}

export interface MBTITestQuestion {
  id: number;
  dimension: string;
  a_pole: string;
  b_pole: string;
  question_zh: string;
  option_a_zh: string;
  option_b_zh: string;
  question_en: string;
  option_a_en: string;
  option_b_en: string;
}

export interface MBTITestResult {
  code: string;
  profile: MBTIType;
  dimensions: MBTIDimensions;
  applied: boolean;
}

export interface MBTIApplyResponse {
  success: boolean;
  code: string;
  message: string;
}
