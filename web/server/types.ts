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
  focusSummary: string;
  sourceIp: string;
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
