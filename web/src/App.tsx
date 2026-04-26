import { FormEvent, useEffect, useState } from "react";
import { getCurrentUser, getDnsDashboard, getProxySetup, login, logout, register, saveConfig } from "./api";
import type { BlockCategory, DnsDashboard, DnsDashboardLog, FocusConfig, ProxySetupInfo, ScheduleWindow, User } from "./types";

const suggestionPrompts = [
  "I am a software engineer working on backend systems, APIs, debugging, and reading technical documentation. GitHub, docs, cloud dashboards, and Stack Overflow are usually on-topic.",
  "I am focused on computer science work like assignments, research, and interview preparation. Entertainment and social apps are usually off-topic during focus time.",
  "I work on startup/product tasks like design docs, coding, analytics, and customer research. Relevant tools and technical references should still count as productive.",
  "I am doing schoolwork and project building. Educational videos or specific communities may be helpful if they clearly relate to my current work."
];

const categories: Array<{ value: BlockCategory; label: string }> = [
  { value: "video-games", label: "Video games" },
  { value: "social-media", label: "Social media" },
  { value: "streaming", label: "Streaming" },
  { value: "shopping", label: "Shopping" },
  { value: "news", label: "News" }
];

const dayLabels = [
  { value: 0, label: "Sun" },
  { value: 1, label: "Mon" },
  { value: 2, label: "Tue" },
  { value: 3, label: "Wed" },
  { value: 4, label: "Thu" },
  { value: 5, label: "Fri" },
  { value: 6, label: "Sat" }
];

const onboardingSteps = [
  { id: "schedule", title: "Focus Calendar" },
  { id: "focus", title: "Focus Prompt" },
  { id: "blacklist", title: "Blacklist" },
  { id: "proxy", title: "Web Proxy" }
] as const;

type SettingsTab = (typeof onboardingSteps)[number]["id"];
type MainTab = "home" | "logs" | SettingsTab;

const SLOT_COUNT = 48;
const SLOT_MINUTES = 30;

function getBrowserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "America/Los_Angeles";
}

function withLocalDefaults(config: FocusConfig): FocusConfig {
  return {
    ...config,
    timezone: config.timezone || getBrowserTimezone()
  };
}

function timeToSlot(value: string): number {
  const [hoursRaw, minutesRaw] = value.split(":");
  const hours = Number(hoursRaw || 0);
  const minutes = Number(minutesRaw || 0);
  const totalMinutes = (hours * 60) + minutes;
  return Math.max(0, Math.min(SLOT_COUNT, Math.round(totalMinutes / SLOT_MINUTES)));
}

function slotToTime(slot: number): string {
  const totalMinutes = slot * SLOT_MINUTES;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function buildWeeklyCells(schedules: ScheduleWindow[]): boolean[][] {
  const grid = Array.from({ length: 7 }, () => Array.from({ length: SLOT_COUNT }, () => false));

  for (const schedule of schedules) {
    const startSlot = Math.max(0, Math.min(SLOT_COUNT - 1, timeToSlot(schedule.start)));
    const endSlot = Math.max(startSlot + 1, Math.min(SLOT_COUNT, timeToSlot(schedule.end)));

    for (const day of schedule.days) {
      if (day < 0 || day > 6) {
        continue;
      }

      for (let slot = startSlot; slot < endSlot; slot += 1) {
        grid[day][slot] = true;
      }
    }
  }

  return grid;
}

function schedulesFromWeeklyCells(cells: boolean[][]): ScheduleWindow[] {
  const schedules: ScheduleWindow[] = [];

  for (let day = 0; day < cells.length; day += 1) {
    let slot = 0;
    while (slot < SLOT_COUNT) {
      if (!cells[day][slot]) {
        slot += 1;
        continue;
      }

      const start = slot;
      while (slot < SLOT_COUNT && cells[day][slot]) {
        slot += 1;
      }

      schedules.push({
        id: crypto.randomUUID(),
        label: "Focus Block",
        days: [day],
        start: slotToTime(start),
        end: slotToTime(slot)
      });
    }
  }

  return schedules;
}

function applySelectionToSchedules(
  schedules: ScheduleWindow[],
  day: number,
  startSlot: number,
  endSlot: number,
  mode: "add" | "remove"
): ScheduleWindow[] {
  const cells = buildWeeklyCells(schedules);
  const rangeStart = Math.max(0, Math.min(startSlot, endSlot));
  const rangeEnd = Math.min(SLOT_COUNT - 1, Math.max(startSlot, endSlot));

  for (let slot = rangeStart; slot <= rangeEnd; slot += 1) {
    cells[day][slot] = mode === "add";
  }

  return schedulesFromWeeklyCells(cells);
}

function formatDaySchedule(day: number, schedules: ScheduleWindow[]): string {
  const matches = schedules
    .filter((entry) => entry.days.includes(day))
    .sort((left, right) => left.start.localeCompare(right.start));

  if (matches.length === 0) {
    return "No focus blocks";
  }

  return matches.map((entry) => `${entry.start}-${entry.end}`).join(", ");
}

function AuthScreen({
  mode,
  onModeChange,
  onSubmit,
  loading,
  error
}: {
  mode: "login" | "register";
  onModeChange: (mode: "login" | "register") => void;
  onSubmit: (username: string, password: string) => Promise<void>;
  loading: boolean;
  error: string | null;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    await onSubmit(username, password);
  }

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <div>
          <p className="eyebrow">TimeHole</p>
          <h1>Set up focus filtering with account-backed preferences.</h1>
        </div>

        <form className="auth-form" onSubmit={handleSubmit}>
          <label>
            Username
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>

          <label>
            Password
            <input
              type="password"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>

          {error ? <div className="error-banner">{error}</div> : null}

          <button type="submit" disabled={loading}>
            {loading
              ? "Working..."
              : mode === "login"
                ? "Sign In"
                : "Create Account"}
          </button>

          <button
            type="button"
            className="secondary-button"
            onClick={() => onModeChange(mode === "login" ? "register" : "login")}
          >
            {mode === "login"
              ? "Need an account? Register"
              : "Already have an account? Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}

function ScheduleEditor({
  config,
  setConfig
}: {
  config: FocusConfig;
  setConfig: (config: FocusConfig) => void;
}) {
  const [dragState, setDragState] = useState<{
    day: number;
    startSlot: number;
    currentSlot: number;
    mode: "add" | "remove";
  } | null>(null);
  const normalizedSchedules = schedulesFromWeeklyCells(buildWeeklyCells(config.schedules));
  const occupiedCells = buildWeeklyCells(normalizedSchedules);
  const previewKeys = new Set<string>();

  if (dragState) {
    const previewStart = Math.min(dragState.startSlot, dragState.currentSlot);
    const previewEnd = Math.max(dragState.startSlot, dragState.currentSlot);
    for (let slot = previewStart; slot <= previewEnd; slot += 1) {
      previewKeys.add(`${dragState.day}-${slot}`);
    }
  }

  useEffect(() => {
    if (!dragState) {
      return undefined;
    }

    const currentDragState = dragState;

    function handlePointerUp() {
      setConfig({
        ...config,
        schedules: applySelectionToSchedules(
          normalizedSchedules,
          currentDragState.day,
          currentDragState.startSlot,
          currentDragState.currentSlot,
          currentDragState.mode
        )
      });
      setDragState(null);
    }

    window.addEventListener("mouseup", handlePointerUp);
    return () => window.removeEventListener("mouseup", handlePointerUp);
  }, [config, dragState, normalizedSchedules, setConfig]);

  return (
    <div className="panel-stack">
      <div className="schedule-block">
        <div className="schedule-header">
          <div>
            <h3>Weekly Focus Calendar</h3>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setConfig({ ...config, schedules: [] })}
          >
            Clear calendar
          </button>
        </div>

        <div className="schedule-calendar-frame">
          <div className="schedule-calendar">
            <div className="schedule-time-column">
              <div className="calendar-corner" />
              {Array.from({ length: SLOT_COUNT }, (_, slot) => (
                <div className="time-label" key={slot}>
                  {slot % 2 === 0 ? slotToTime(slot) : ""}
                </div>
              ))}
            </div>

            {dayLabels.map((day) => (
              <div className="schedule-day-column" key={day.value}>
                <div className="calendar-day-heading">
                  <strong>{day.label}</strong>
                </div>

                {Array.from({ length: SLOT_COUNT }, (_, slot) => {
                  const occupied = occupiedCells[day.value][slot];
                  const inPreview = previewKeys.has(`${day.value}-${slot}`);
                  const previewMode = dragState?.mode;

                  return (
                    <button
                      key={`${day.value}-${slot}`}
                      type="button"
                      className={`calendar-slot ${occupied ? "occupied" : ""} ${inPreview ? "preview" : ""} ${previewMode === "remove" && inPreview ? "preview-remove" : ""}`}
                      onMouseDown={() =>
                        setDragState({
                          day: day.value,
                          startSlot: slot,
                          currentSlot: slot,
                          mode: occupied ? "remove" : "add"
                        })
                      }
                      onMouseEnter={() => {
                        if (dragState && dragState.day === day.value) {
                          setDragState({ ...dragState, currentSlot: slot });
                        }
                      }}
                    />
                  );
                })}
              </div>
            ))}
          </div>
        </div>

        <div className="schedule-legend">
          <span><i className="legend-swatch selected" /> Focus block</span>
          <span><i className="legend-swatch preview" /> Drag preview</span>
          <span><i className="legend-swatch remove" /> Drag over an existing block to remove it</span>
        </div>

        <div className="schedule-summary-list">
          {dayLabels.map((day) => (
            <div key={day.value} className="summary-row">
              <strong>{day.label}</strong>
              <span>{formatDaySchedule(day.value, normalizedSchedules)}</span>
            </div>
          ))}
        </div>

        {normalizedSchedules.length === 0 ? (
          <div className="empty-state">
            No work blocks yet. Drag over the weekly calendar to create your first focus block.
          </div>
        ) : null}

        <div className="meta-box">
          <span>Timezone used for schedule enforcement</span>
          <strong>{config.timezone}</strong>
        </div>
      </div>
    </div>
  );
}

function FocusEditor({
  config,
  setConfig
}: {
  config: FocusConfig;
  setConfig: (config: FocusConfig) => void;
}) {
  return (
    <div className="panel-stack focus-editor">
      <label>
        What do you want to focus on, and what do you want to stay away from?
        <textarea
          className="focus-summary-input"
          rows={6}
          value={config.focusSummary}
          onChange={(event) => setConfig({ ...config, focusSummary: event.target.value })}
          placeholder="I want to focus on coursework, project work, and documentation. I want to stay away from social feeds, gaming, and casual browsing during work blocks..."
        />
      </label>

      <div className="suggestions-layout">
        <div className="suggestions-copy">
          <h3>Prompt Suggestions</h3>
        </div>
        <div className="suggestions">
          {suggestionPrompts.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="suggestion-chip"
              onClick={() =>
                setConfig({
                  ...config,
                  focusSummary: suggestion
                })
              }
            >
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function BlacklistEditor({
  config,
  setConfig,
  blacklistDraft,
  setBlacklistDraft
}: {
  config: FocusConfig;
  setConfig: (config: FocusConfig) => void;
  blacklistDraft: string;
  setBlacklistDraft: (value: string) => void;
}) {
  return (
    <div className="panel-stack">
      <label>
        Blacklist entries, one per line
        <textarea
          rows={8}
          value={blacklistDraft}
          onChange={(event) => setBlacklistDraft(event.target.value)}
          placeholder={"tiktok\nreddit\nroblox"}
        />
      </label>

      <div className="panel-copy">
        <h3>Categories</h3>
      </div>
      <div className="category-grid">
        {categories.map((category) => (
          <label className="category-pill" key={category.value}>
            <input
              type="checkbox"
              checked={config.blockedCategories.includes(category.value)}
              onChange={(event) =>
                setConfig({
                  ...config,
                  blockedCategories: event.target.checked
                    ? [...config.blockedCategories, category.value]
                    : config.blockedCategories.filter((value) => value !== category.value)
                })
              }
            />
            <span>{category.label}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

function ProxySetupEditor({
  proxySetup,
  proxyLoading,
  proxyError
}: {
  proxySetup: ProxySetupInfo | null;
  proxyLoading: boolean;
  proxyError: string | null;
}) {
  return (
    <div className="panel-stack">
      {proxyLoading ? <div className="empty-state">Loading proxy setup instructions...</div> : null}
      {proxyError ? <div className="error-banner">{proxyError}</div> : null}

      {proxySetup ? (
        <>
          <div className="proxy-setup-grid">
            <div className="meta-box">
              <span>Proxy address</span>
              <strong>{proxySetup.proxyUrl}</strong>
            </div>
            <div className="meta-box">
              <span>HTTPS inspection</span>
              <strong>{proxySetup.mitmEnabled ? "Enabled" : "Disabled"}</strong>
            </div>
          </div>

          <div className="dashboard-panel">
            <div className="panel-copy">
              <h3>1. Configure your browser proxy</h3>
              <p>Point both HTTP and HTTPS proxy traffic at the gateway proxy port.</p>
            </div>
            <ol className="instructions-list">
              {proxySetup.browserSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>

          <div className="dashboard-panel">
            <div className="panel-copy">
              <h3>2. Install the TimeHole root CA</h3>
              <p>This allows the proxy to terminate TLS locally so it can inspect HTTPS URLs and responses.</p>
            </div>
            <a className="download-link" href={proxySetup.caDownloadUrl} target="_blank" rel="noreferrer">
              Download root CA certificate
            </a>
            <ol className="instructions-list">
              {proxySetup.certificateSteps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
        </>
      ) : null}
    </div>
  );
}

function SettingsSection({
  tab,
  config,
  setConfig,
  blacklistDraft,
  setBlacklistDraft,
  proxySetup,
  proxyLoading,
  proxyError
}: {
  tab: SettingsTab;
  config: FocusConfig;
  setConfig: (config: FocusConfig) => void;
  blacklistDraft: string;
  setBlacklistDraft: (value: string) => void;
  proxySetup: ProxySetupInfo | null;
  proxyLoading: boolean;
  proxyError: string | null;
}) {
  if (tab === "schedule") {
    return <ScheduleEditor config={config} setConfig={setConfig} />;
  }

  if (tab === "focus") {
    return <FocusEditor config={config} setConfig={setConfig} />;
  }

  if (tab === "proxy") {
    return (
      <ProxySetupEditor
        proxySetup={proxySetup}
        proxyLoading={proxyLoading}
        proxyError={proxyError}
      />
    );
  }

  return (
    <BlacklistEditor
      config={config}
      setConfig={setConfig}
      blacklistDraft={blacklistDraft}
      setBlacklistDraft={setBlacklistDraft}
    />
  );
}

function LogsTable({
  title,
  description,
  logs,
  sortField,
  sortOrder,
  onSort,
  expandedLogs,
  onToggleExpansion,
  idPrefix
}: {
  title: string;
  description: string;
  logs: DnsDashboardLog[];
  sortField: string;
  sortOrder: "asc" | "desc";
  onSort: (field: any) => void;
  expandedLogs: Set<string>;
  onToggleExpansion: (id: string) => void;
  idPrefix: string;
}) {
  const sortedLogs = [...logs].sort((a, b) => {
    let valA: any = sortField === "time" ? a.createdAt : (a as any)[sortField];
    let valB: any = sortField === "time" ? b.createdAt : (b as any)[sortField];

    if (valA == null) valA = "";
    if (valB == null) valB = "";

    if (valA < valB) return sortOrder === "asc" ? -1 : 1;
    if (valA > valB) return sortOrder === "asc" ? 1 : -1;
    return 0;
  });

  return (
    <div className="dashboard-panel logs-panel">
      <div className="panel-copy">
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      {logs.length === 0 ? (
        <div className="empty-state">No recent log entries for this category yet.</div>
      ) : (
        <div className="table-container">
          <table className="logs-table">
            <thead>
              <tr>
                <th onClick={() => onSort("time")}>Time {sortField === "time" && (sortOrder === "asc" ? "↑" : "↓")}</th>
                <th onClick={() => onSort("queryType")}>Type {sortField === "queryType" && (sortOrder === "asc" ? "↑" : "↓")}</th>
                <th onClick={() => onSort("queryName")}>Target {sortField === "queryName" && (sortOrder === "asc" ? "↑" : "↓")}</th>
                <th onClick={() => onSort("blocked")}>Status {sortField === "blocked" && (sortOrder === "asc" ? "↑" : "↓")}</th>
              </tr>
            </thead>
            <tbody>
              {sortedLogs.map((log, index) => {
                const logId = `${idPrefix}-${log.createdAt}-${index}`;
                const isExpanded = expandedLogs.has(logId);
                return (
                  <tr
                    key={logId}
                    className={`${log.blocked ? "blocked" : "allowed"} ${isExpanded ? "expanded" : ""}`}
                    onClick={() => onToggleExpansion(logId)}
                  >
                    <td className="nowrap">{new Date(log.createdAt).toLocaleString()}</td>
                    <td><span className="type-badge">{log.queryType}</span></td>
                    <td className="domain-cell">
                      <strong className={isExpanded ? "" : "clamped"}>{log.queryName}</strong>
                    </td>
                    <td>
                      <span className={`status-pill ${log.blocked ? "blocked" : "allowed"}`}>
                        {log.blocked ? "Blocked" : "OK"}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LogsView({
  dashboard
}: {
  dashboard: DnsDashboard | null;
}) {
  const [expandedLogs, setExpandedLogs] = useState<Set<string>>(new Set());
  const [dnsSortField, setDnsSortField] = useState<string>("time");
  const [dnsSortOrder, setDnsSortOrder] = useState<"asc" | "desc">("desc");
  const [proxySortField, setProxySortField] = useState<string>("time");
  const [proxySortOrder, setProxySortOrder] = useState<"asc" | "desc">("desc");

  const toggleLogExpansion = (id: string) => {
    const next = new Set(expandedLogs);
    if (next.has(id)) {
      next.delete(id);
    } else {
      next.add(id);
    }
    setExpandedLogs(next);
  };

  if (!dashboard) {
    return <div className="empty-state">No gateway metrics yet for this source IP.</div>;
  }

  const handleDnsSort = (field: string) => {
    if (dnsSortField === field) {
      setDnsSortOrder(dnsSortOrder === "asc" ? "desc" : "asc");
    } else {
      setDnsSortField(field);
      setDnsSortOrder("desc");
    }
  };

  const handleProxySort = (field: string) => {
    if (proxySortField === field) {
      setProxySortOrder(proxySortOrder === "asc" ? "desc" : "asc");
    } else {
      setProxySortField(field);
      setProxySortOrder("desc");
    }
  };

  return (
    <div className="logs-view">
      <LogsTable
        title="DNS Traffic Logs"
        description="Low-level network requests captured by the DNS relay."
        logs={dashboard.recentDnsLogs}
        sortField={dnsSortField}
        sortOrder={dnsSortOrder}
        onSort={handleDnsSort}
        expandedLogs={expandedLogs}
        onToggleExpansion={toggleLogExpansion}
        idPrefix="dns"
      />
      <LogsTable
        title="Web Proxy Logs"
        description="HTTP and HTTPS application-layer traffic inspected by the gateway."
        logs={dashboard.recentProxyLogs}
        sortField={proxySortField}
        sortOrder={proxySortOrder}
        onSort={handleProxySort}
        expandedLogs={expandedLogs}
        onToggleExpansion={toggleLogExpansion}
        idPrefix="proxy"
      />
    </div>
  );
}

function DashboardHome({
  dashboard,
  focusModeEnabled,
  onToggleFocusMode,
  togglingFocusMode
}: {
  dashboard: DnsDashboard | null;
  focusModeEnabled: boolean;
  onToggleFocusMode: () => Promise<void>;
  togglingFocusMode: boolean;
}) {
  if (!dashboard) {
    return (
      <div className="panel-stack">
        <div className="focus-mode-banner">
          <div>
            <h3>Manual Focus Mode</h3>
            <p>{focusModeEnabled ? "Focus mode is enabled, your activity will be filtered until disabled." : "Focus mode is disabled, so filtering follows your calendar."}</p>
          </div>
          <button type="button" onClick={() => void onToggleFocusMode()} disabled={togglingFocusMode}>
            {togglingFocusMode
              ? "Updating..."
              : focusModeEnabled
                ? "Disable focus mode"
                : "Enable focus mode"}
          </button>
        </div>
        <div className="empty-state">No gateway metrics yet for this source IP.</div>
      </div>
    );
  }

  return (
    <div className="panel-stack">
      <div className="focus-mode-banner">
        <div>
          <h3>Manual Focus Mode</h3>
          <p>{focusModeEnabled ? "Focus mode is enabled, your activity will be filtered until disabled." : "Focus mode is disabled, so filtering follows your calendar."}</p>
        </div>
        <button type="button" onClick={() => void onToggleFocusMode()} disabled={togglingFocusMode}>
          {togglingFocusMode
            ? "Updating..."
            : focusModeEnabled
              ? "Disable focus mode"
              : "Enable focus mode"}
        </button>
      </div>

      <div className="metrics-grid">
        <div className="metric-card" style={{ background: "rgba(125, 211, 252, 0.8)" }}>
          <span>Total requests</span>
          <strong>{dashboard.totals.totalQueries}</strong>
        </div>
        <div className="metric-card" style={{ background: "rgba(134, 239, 172, 0.8)" }}>
          <span>Allowed requests</span>
          <strong>{dashboard.totals.allowedQueries}</strong>
        </div>
        <div className="metric-card" style={{ background: "rgba(252, 165, 165, 0.8)" }}>
          <span>Blocked requests</span>
          <strong>{dashboard.totals.blockedQueries}</strong>
        </div>
        <div className="metric-card" style={{ background: "rgba(253, 224, 71, 0.8)" }}>
          <span>Block rate</span>
          <strong>{(dashboard.totals.blockRate * 100).toFixed(0)}%</strong>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-panel">
          <div className="panel-copy">
            <h3>Top blocked domains</h3>
          </div>
          {dashboard.topBlockedDomains.length === 0 ? (
            <div className="empty-state">No blocked DNS domains yet.</div>
          ) : (
            <div className="bars-list blocked-domains-list">
              {dashboard.topBlockedDomains.slice(0, 4).map((entry) => (
                <div className="bar-row blocked-domain-row" key={entry.queryName}>
                  <div className="bar-row-meta">
                    <span>{entry.queryName}</span>
                    <strong>{entry.count}</strong>
                  </div>
                  <div className="bar-track">
                    <div
                      className="bar-fill"
                      style={{
                        width: `${(entry.count / dashboard.topBlockedDomains[0].count) * 100}%`
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="dashboard-panel">
          <div className="panel-copy">
            <h3>Recent hourly activity</h3>
          </div>
          {dashboard.recentActivity.length === 0 ? (
            <div className="empty-state">No hourly DNS activity has been logged yet.</div>
          ) : (
            <div className="activity-list">
              {dashboard.recentActivity.map((bucket) => (
                <div className="activity-row" key={bucket.hour}>
                  <div className="activity-row-meta">
                    <span>{new Date(bucket.hour).toLocaleString()}</span>
                    <strong>{bucket.total} total</strong>
                  </div>
                  <div className="activity-stats">
                    <span>{bucket.blocked} blocked</span>
                    <span>{bucket.total - bucket.blocked} allowed</span>
                  </div>
                  <div className="bar-track">
                    <div
                      className="bar-fill"
                      style={{
                        width: `${(bucket.total / dashboard.recentActivity.reduce((max, item) => Math.max(max, item.total), 1)) * 100}%`
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [config, setConfig] = useState<FocusConfig | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register">("register");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blacklistDraft, setBlacklistDraft] = useState("");
  const [isOnboarding, setIsOnboarding] = useState(false);
  const [activeTab, setActiveTab] = useState<MainTab>("home");
  const [dashboard, setDashboard] = useState<DnsDashboard | null>(null);
  const [togglingFocusMode, setTogglingFocusMode] = useState(false);
  const [proxySetup, setProxySetup] = useState<ProxySetupInfo | null>(null);
  const [proxyLoading, setProxyLoading] = useState(false);
  const [proxyError, setProxyError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const response = await getCurrentUser();
        if (response.user) {
          setUser(response.user);
          setConfig(withLocalDefaults(response.user.focusConfig));
          setBlacklistDraft(response.user.focusConfig.blacklist.join("\n"));
          setIsOnboarding(false);
          setActiveTab("home");
          try {
            setDashboard(await getDnsDashboard());
          } catch {
            setDashboard(null);
          }
          setProxyLoading(true);
          try {
            setProxySetup(await getProxySetup());
            setProxyError(null);
          } catch (nextError) {
            setProxyError(nextError instanceof Error ? nextError.message : "Could not load proxy setup.");
          } finally {
            setProxyLoading(false);
          }
        }
      } catch {
        // ignore unauthenticated initial state
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!user) {
      return;
    }

    let cancelled = false;
    async function refreshDashboard() {
      try {
        const nextDashboard = await getDnsDashboard();
        if (!cancelled) {
          setDashboard(nextDashboard);
        }
      } catch {
        if (!cancelled) {
          setDashboard(null);
        }
      }
    }

    void refreshDashboard();
    const intervalId = window.setInterval(() => {
      void refreshDashboard();
    }, 5000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [user]);

  async function handleAuth(username: string, password: string) {
    setLoading(true);
    setError(null);
    try {
      const response =
        authMode === "login"
          ? await login(username, password)
          : await register(username, password);
      setUser(response.user);
      setConfig(withLocalDefaults(response.user.focusConfig));
      setBlacklistDraft(response.user.focusConfig.blacklist.join("\n"));
      setActiveTab(authMode === "register" ? "schedule" : "home");
      setIsOnboarding(authMode === "register");
      try {
        setDashboard(await getDnsDashboard());
      } catch {
        setDashboard(null);
      }
      setProxyLoading(true);
      try {
        setProxySetup(await getProxySetup());
        setProxyError(null);
      } catch (nextError) {
        setProxyError(nextError instanceof Error ? nextError.message : "Could not load proxy setup.");
      } finally {
        setProxyLoading(false);
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Authentication failed.");
    } finally {
      setLoading(false);
    }
  }

  async function handleLogout() {
    await logout();
    setUser(null);
    setConfig(null);
    setBlacklistDraft("");
    setError(null);
    setIsOnboarding(false);
    setActiveTab("home");
    setDashboard(null);
    setProxySetup(null);
    setProxyError(null);
  }

  async function persistConfig(nextConfig: FocusConfig) {
    const payload: FocusConfig = {
      ...nextConfig,
      timezone: nextConfig.timezone || getBrowserTimezone(),
      blacklist: blacklistDraft
        .split(/\r?\n/)
        .map((entry) => entry.trim())
        .filter(Boolean)
    };
    const response = await saveConfig(payload);
    setConfig(response.config);
    setBlacklistDraft(response.config.blacklist.join("\n"));
    try {
      setDashboard(await getDnsDashboard());
    } catch {
      setDashboard(null);
    }
  }

  async function handleToggleFocusMode() {
    if (!config) {
      return;
    }

    setTogglingFocusMode(true);
    setError(null);
    try {
      await persistConfig({
        ...config,
        studyModeEnabled: !config.studyModeEnabled,
        timezone: config.timezone || getBrowserTimezone()
      });
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Could not update focus mode.");
    } finally {
      setTogglingFocusMode(false);
    }
  }

  async function handleSave(event: FormEvent) {
    event.preventDefault();
    if (!config) {
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await persistConfig(config);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  async function handleOnboardingNext(event: FormEvent) {
    event.preventDefault();
    if (!config) {
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await persistConfig(config);
      const currentIndex = onboardingSteps.findIndex((step) => step.id === activeTab);
      const nextStep = onboardingSteps[currentIndex + 1];
      if (nextStep) {
        setActiveTab(nextStep.id);
      } else {
        setIsOnboarding(false);
      }
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  }

  if (loading && !config) {
    return <main className="auth-shell">Loading...</main>;
  }

  if (!user || !config) {
    return (
      <AuthScreen
        mode={authMode}
        onModeChange={setAuthMode}
        onSubmit={handleAuth}
        loading={loading}
        error={error}
      />
    );
  }

  const currentStepIndex = onboardingSteps.findIndex((step) => step.id === activeTab);
  const currentStep = onboardingSteps[currentStepIndex];

  return (
    <main className="app-shell">
      {isOnboarding ? (
        <form className="wizard-shell" onSubmit={handleOnboardingNext}>
          <aside className="card wizard-steps">
            <div className="meta-box" style={{ border: "none", background: "none", padding: "0 0 12px 0" }}>
              <p className="eyebrow" style={{ margin: 0 }}>TimeHole</p>
            </div>
            {onboardingSteps.map((step, index) => (
              <div
                className={`wizard-step ${step.id === activeTab ? "active" : ""} ${index < currentStepIndex ? "complete" : ""}`}
                key={step.id}
              >
                <span className="wizard-index">{index + 1}</span>
                <div>
                  <strong>{step.title}</strong>
                  {step.id === "proxy" ? <p>Optionally configure the browser proxy and HTTPS certificate.</p> : null}
                </div>
              </div>
            ))}
            <button className="secondary-button" type="button" onClick={handleLogout} style={{ marginTop: "12px" }}>
              Sign out
            </button>
          </aside>

          <section className="card panel wizard-panel">
            <div className="panel-header">
              <h2>{currentStep.title}</h2>
              <p>
                {currentStep.id === "schedule" &&
                  "First, assign the weekly work blocks when focus mode should be active."}
                {currentStep.id === "focus" &&
                  "Next, explain what you want to focus on and what you want to stay away from."}
                {currentStep.id === "blacklist" &&
                  "Now choose blocked categories and add any manual blacklist entries."}
                {currentStep.id === "proxy" &&
                  "Optionally finish by enabling browser proxying and installing the TimeHole root certificate for HTTPS inspection."}
              </p>
            </div>

            <SettingsSection
              tab={activeTab as SettingsTab}
              config={config}
              setConfig={setConfig}
              blacklistDraft={blacklistDraft}
              setBlacklistDraft={setBlacklistDraft}
              proxySetup={proxySetup}
              proxyLoading={proxyLoading}
              proxyError={proxyError}
            />

            {error ? <div className="error-banner">{error}</div> : null}

            <div className="wizard-actions">
              {currentStepIndex > 0 ? (
                <button
                  type="button"
                  className="secondary-button"
                  disabled={saving}
                  onClick={() => {
                    setActiveTab(onboardingSteps[currentStepIndex - 1].id);
                  }}
                >
                  Back
                </button>
              ) : (
                <div />
              )}
              <button type="submit" disabled={saving}>
                {saving
                  ? "Saving..."
                  : currentStepIndex === onboardingSteps.length - 1
                    ? "Finish"
                    : "Next"}
              </button>
            </div>
          </section>
        </form>
      ) : (
        <form className="tabs-shell" onSubmit={handleSave}>
          <nav className="card tabs-nav" aria-label="Settings tabs">
            <div className="meta-box" style={{ border: "none", background: "none", padding: "0 0 12px 0" }}>
              <p className="eyebrow" style={{ margin: 0 }}>TimeHole</p>
            </div>
            {[{ id: "home", title: "Home" } as const, { id: "logs", title: "Logs" } as const, ...onboardingSteps].map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={`tab-button ${activeTab === tab.id ? "active" : ""}`}
                onClick={() => setActiveTab(tab.id as MainTab)}
              >
                {tab.title}
              </button>
            ))}
            <button className="secondary-button" type="button" onClick={handleLogout} style={{ marginTop: "auto", paddingTop: "12px" }}>
              Sign out
            </button>
          </nav>

          <section className={`card panel tab-panel ${activeTab === "logs" ? "logs-tab-panel" : ""}`}>
            {activeTab !== "logs" ? (
              <div className="panel-header">
                <h2>
                  {activeTab === "home"
                    ? "TimeHole"
                    : onboardingSteps.find((tab) => tab.id === activeTab)?.title}
                </h2>
              </div>
            ) : null}

            {activeTab === "home" ? (
              <DashboardHome
                dashboard={dashboard}
                focusModeEnabled={config.studyModeEnabled}
                onToggleFocusMode={handleToggleFocusMode}
                togglingFocusMode={togglingFocusMode}
              />
            ) : activeTab === "logs" ? (
              <LogsView dashboard={dashboard} />
            ) : (
              <>
                <SettingsSection
                  tab={activeTab as SettingsTab}
                  config={config}
                  setConfig={setConfig}
                  blacklistDraft={blacklistDraft}
                  setBlacklistDraft={setBlacklistDraft}
                  proxySetup={proxySetup}
                  proxyLoading={proxyLoading}
                  proxyError={proxyError}
                />

                {activeTab !== "proxy" ? (
                  <div className="save-row">
                    <div>
                      <h3>Save changes</h3>
                    </div>
                    <button type="submit" disabled={saving}>
                      {saving ? "Saving..." : "Save settings"}
                    </button>
                  </div>
                ) : null}
              </>
            )}

            {error ? <div className="error-banner">{error}</div> : null}
          </section>
        </form>
      )}
    </main>
  );
}
