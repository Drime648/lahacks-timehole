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
  manualBlacklist?: string[];
  categoryBlacklist?: string[];
  focusSummary: string;
  sourceIp: string;
  timezone: string;
  updatedAt: string;
}

export interface User {
  username: string;
  createdAt: string;
  updatedAt: string;
  registrationIp: string;
  lastLoginIp: string;
  focusConfig: FocusConfig;
}

export interface DnsDashboardLog {
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

export interface DnsDashboard {
  sourceIp: string;
  totals: {
    totalQueries: number;
    blockedQueries: number;
    allowedQueries: number;
    cacheHits: number;
    cacheHitRate: number;
    blockRate: number;
    uniqueDomains: number;
    avgLatencyMs: number | null;
  };
  topQueriedDomains: Array<{
    queryName: string;
    count: number;
  }>;
  topBlockedDomains: Array<{
    queryName: string;
    count: number;
  }>;
  queryTypeBreakdown: Array<{
    queryType: string;
    count: number;
  }>;
  decisionBreakdown: Array<{
    decisionReason: string;
    count: number;
  }>;
  recentActivity: Array<{
    hour: string;
    total: number;
    blocked: number;
  }>;
  recentLogs: DnsDashboardLog[];
}

export interface ProxySetupInfo {
  proxyHost: string;
  proxyPort: number;
  proxyUrl: string;
  caDownloadUrl: string;
  mitmEnabled: boolean;
  browserSteps: string[];
  certificateSteps: string[];
}
