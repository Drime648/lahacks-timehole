export type BlockCategory =
  | "video-games"
  | "social-media"
  | "streaming"
  | "shopping"
  | "news";

export interface ScheduleWindow {
  id: string;
  label: string;
  days: number[];
  start: string;
  end: string;
}

export interface FocusConfig {
  studyModeEnabled: boolean;
  schedules: ScheduleWindow[];
  blockedCategories: BlockCategory[];
  blacklist: string[];
  manualBlacklist: string[];
  categoryBlacklist: string[];
  focusSummary: string;
  sourceIp: string;
  timezone: string;
  updatedAt: string;
}

export interface UserDocument {
  username: string;
  passwordHash: string;
  createdAt: string;
  updatedAt: string;
  registrationIp: string;
  lastLoginIp: string;
  focusConfig: FocusConfig;
}

export interface DnsLogDocument {
  sourceIp: string;
  username?: string | null;
  userMatched?: boolean;
  queryName: string;
  queryType: string;
  blocked: boolean;
  cacheHit: boolean;
  decisionReason?: string;
  blacklistSize?: number;
  responseCode?: string | null;
  answerCount?: number;
  answers?: string[];
  upstreamLatencyMs?: number | null;
  error?: string | null;
  createdAt: string;
}
