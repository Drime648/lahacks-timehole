import { FormEvent, useEffect, useState } from "react";
import { getCurrentUser, getDnsDashboard, getProxySetup, login, logout, register, saveConfig } from "./api";
import type { BlockCategory, DnsDashboard, FocusConfig, ProxySetupInfo, ScheduleWindow, User } from "./types";

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
  { id: "categories", title: "Categories" },
  { id: "blacklist", title: "Manual Blacklist" },
  { id: "proxy", title: "Web Proxy" }
] as const;

type SettingsTab = (typeof onboardingSteps)[number]["id"];
type MainTab = "home" | SettingsTab;

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

function promptPreview(config: FocusConfig): string {
  return `Given these categories that should be blocked: ${config.blockedCategories.join(", ") || "none"}, this user focus description: ${config.focusSummary || "(empty)"}, and this request data, is this on topic? Answer yes or no with a short reason.`;
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
          <p className="lede">
            Create an account to walk through your schedule, focus goals, blocked
            categories, and manual blacklist, then save it all to MongoDB.
          </p>
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
            <p>Click and drag down a day column to mark when filtering should be active.</p>
          </div>
          <button
            type="button"
            className="secondary-button"
            onClick={() => setConfig({ ...config, schedules: [] })}
          >
            Clear calendar
          </button>
        </div>

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
    <div className="panel-stack">
      <label>
        What do you want to focus on, and what do you want to stay away from?
        <textarea
          rows={12}
          value={config.focusSummary}
          onChange={(event) => setConfig({ ...config, focusSummary: event.target.value })}
          placeholder="I want to focus on coursework, project work, and documentation. I want to stay away from social feeds, gaming, and casual browsing during work blocks..."
        />
      </label>

      <div className="suggestions-layout">
        <div className="suggestions-copy">
          <h3>Prompt Suggestions</h3>
          <p>Click a suggestion to use it as your focus description.</p>
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

function CategoriesEditor({
  config,
  setConfig
}: {
  config: FocusConfig;
  setConfig: (config: FocusConfig) => void;
}) {
  return (
    <div className="panel-stack">
      <div className="panel-copy">
        <h3>Choose categories to block during work time</h3>
        <p>Select the types of browsing you want the future filtering system to treat as off-topic.</p>
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

function BlacklistEditor({
  blacklistDraft,
  setBlacklistDraft
}: {
  blacklistDraft: string;
  setBlacklistDraft: (value: string) => void;
}) {
  return (
    <div className="panel-stack">
      <div className="panel-copy">
        <p>Add one blacklist entry per line for sites you always want blocked, like TikTok or Reddit.</p>
      </div>
      <label>
        Blacklist entries, one per line
        <textarea
          rows={8}
          value={blacklistDraft}
          onChange={(event) => setBlacklistDraft(event.target.value)}
          placeholder={"tiktok\nreddit\nroblox"}
        />
      </label>
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
      <div className="panel-copy">
        <h3>Browser Proxy Setup</h3>
        <p>
          This step is optional, but it is what enables HTTP and HTTPS layer 7 inspection in the web proxy.
        </p>
      </div>

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

  if (tab === "categories") {
    return <CategoriesEditor config={config} setConfig={setConfig} />;
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
      blacklistDraft={blacklistDraft}
      setBlacklistDraft={setBlacklistDraft}
    />
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
  const [expandedLogs, setExpandedLogs] = useState<Set<number>>(new Set());

  const toggleLogExpansion = (index: number) => {
    const next = new Set(expandedLogs);
    if (next.has(index)) {
      next.delete(index);
    } else {
      next.add(index);
    }
    setExpandedLogs(next);
  };

  if (!dashboard) {
    return (
      <div className="panel-stack">
        <div className="focus-mode-banner">
          <div>
            <h3>Manual Focus Mode</h3>
            <p>Turn filtering on immediately, even outside your calendar blocks.</p>
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
          <p>{focusModeEnabled ? "Filtering is currently forced on." : "Filtering will only run during your focus calendar unless you manually enable it here."}</p>
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
            <h3>Top DNS domains</h3>
            <p>Most frequently requested DNS domains for your source IP.</p>
          </div>
          {dashboard.topQueriedDomains.length === 0 ? (
            <div className="empty-state">No DNS domains queried yet.</div>
          ) : (
            <div className="bars-list">
              {dashboard.topQueriedDomains.map((entry) => (
                <div className="bar-row" key={entry.queryName}>
                  <div className="bar-row-meta">
                    <span>{entry.queryName}</span>
                    <strong>{entry.count}</strong>
                  </div>
                  <div className="bar-track">
                    <div
                      className="bar-fill secondary"
                      style={{
                        width: `${(entry.count / dashboard.topQueriedDomains[0].count) * 100}%`
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
            <h3>Top blocked domains</h3>
            <p>Most frequently blackholed DNS lookups for your current source IP.</p>
          </div>
          {dashboard.topBlockedDomains.length === 0 ? (
            <div className="empty-state">No blocked DNS domains yet.</div>
          ) : (
            <div className="bars-list">
              {dashboard.topBlockedDomains.map((entry) => (
                <div className="bar-row" key={entry.queryName}>
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
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-panel">
          <div className="panel-copy">
            <h3>Decision breakdown</h3>
            <p>Why the relay allowed, blocked, or failed each DNS request.</p>
          </div>
          {dashboard.decisionBreakdown.length === 0 ? (
            <div className="empty-state">No DNS decisions recorded yet.</div>
          ) : (
            <div className="bars-list">
              {dashboard.decisionBreakdown.map((entry) => (
                <div className="bar-row" key={entry.decisionReason}>
                  <div className="bar-row-meta">
                    <span>{entry.decisionReason}</span>
                    <strong>{entry.count}</strong>
                  </div>
                  <div className="bar-track">
                    <div
                      className="bar-fill secondary"
                      style={{
                        width: `${(entry.count / dashboard.decisionBreakdown[0].count) * 100}%`
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
            <p>A simple recent timeline of DNS traffic and blocked volume.</p>
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

      <div className="dashboard-panel">
        <div className="panel-copy">
          <h3>Query type breakdown</h3>
          <p>Distribution of DNS request types passing through the relay.</p>
        </div>
        {dashboard.queryTypeBreakdown.length === 0 ? (
          <div className="empty-state">No query types recorded yet.</div>
        ) : (
          <div className="bars-list">
            {dashboard.queryTypeBreakdown.map((entry) => (
              <div className="bar-row" key={entry.queryType}>
                <div className="bar-row-meta">
                  <span>{entry.queryType}</span>
                  <strong>{entry.count}</strong>
                </div>
                <div className="bar-track">
                  <div
                    className="bar-fill"
                    style={{
                      width: `${(entry.count / dashboard.queryTypeBreakdown[0].count) * 100}%`
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
          <h3>Recent DNS query log</h3>
          <p>Pi-hole-style per-query detail for the most recent relay decisions.</p>
        </div>
        {dashboard.recentLogs.length === 0 ? (
          <div className="empty-state">No recent DNS query log entries yet.</div>
        ) : (
          <div className="logs-list">
            {dashboard.recentLogs.map((log, index) => {
              const isExpanded = expandedLogs.has(index);
              return (
                <div
                  className={`log-row ${log.blocked ? "blocked" : "allowed"} ${isExpanded ? "expanded" : ""}`}
                  key={`${log.createdAt}-${index}`}
                  onClick={() => toggleLogExpansion(index)}
                  style={{ cursor: "pointer" }}
                >
                  <div className="log-row-top">
                    <strong className={isExpanded ? "" : "clamped"}>
                      {log.queryName}
                    </strong>
                    <span>{log.queryType}</span>
                  </div>
                  <div className="log-row-meta-grid">
                    <span className="status">Status: {log.blocked ? "Blocked" : "Allowed"}</span>
                    <span className="cache">Cache: {log.cacheHit ? "hit" : "miss"}</span>
                    <span className="reason">Reason: {log.decisionReason || "n/a"}</span>
                    <span className="code">HTTP {log.responseCode || "n/a"}</span>
                    <span className="latency">Latency: {log.upstreamLatencyMs != null ? `${log.upstreamLatencyMs} ms` : "n/a"}</span>
                    <span className="timestamp">Date: {new Date(log.createdAt).toLocaleString()}</span>
                    <span className="user">User: {log.userMatched ? log.username : "none"}</span>
                    <span className="answers">{log.answerCount ?? 0} {log.answerCount == 1 ? "answer" : "answers"}</span>
                    <span className="details">Responses: {(log.answers || []).slice(0, 3).join(", ") || "none"}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
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
              <p className="eyebrow" style={{ margin: 0 }}>{user.username}</p>
              <strong style={{ fontSize: "0.8rem", opacity: 0.8 }}>{config.sourceIp}</strong>
            </div>
            {onboardingSteps.map((step, index) => (
              <div
                className={`wizard-step ${step.id === activeTab ? "active" : ""} ${index < currentStepIndex ? "complete" : ""}`}
                key={step.id}
              >
                <span className="wizard-index">{index + 1}</span>
                <div>
                  <strong>{step.title}</strong>
                  <p>
                    {step.id === "schedule" && "Choose when focus filtering should be active."}
                    {step.id === "focus" && "Describe what you want to focus on and avoid."}
                    {step.id === "categories" && "Select the types of content to block."}
                    {step.id === "blacklist" && "Add exact site fragments to blackhole."}
                    {step.id === "proxy" && "Optionally configure the browser proxy and HTTPS certificate."}
                  </p>
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
                {currentStep.id === "categories" &&
                  "Now choose the categories that should be blocked during those work blocks."}
                {currentStep.id === "blacklist" &&
                  "Finally, add any manual blacklist entries for sites you always want blocked."}
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
              <p className="eyebrow" style={{ margin: 0 }}>{user.username}</p>
              <strong style={{ fontSize: "0.8rem", opacity: 0.8 }}>{config.sourceIp}</strong>
            </div>
            {[{ id: "home", title: "Home" } as const, ...onboardingSteps].map((tab) => (
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

          <section className="card panel tab-panel">
            <div className="panel-header">
              <h2>
                {activeTab === "home"
                  ? "Home"
                  : onboardingSteps.find((tab) => tab.id === activeTab)?.title}
              </h2>
              <p>
                {activeTab === "home" &&
                  "Your DNS relay metrics and recent DNS activity appear here by default."}
                {activeTab === "schedule" &&
                  "The previous main settings content now lives here under Focus Calendar."}
                {activeTab === "focus" &&
                  "Refine the paragraph that explains what productive work looks like for you."}
                {activeTab === "categories" &&
                  "Adjust the categories that should be considered off-topic during focus time."}
                {activeTab === "proxy" &&
                  "Download the root CA, enable the browser proxy, and turn on HTTPS layer 7 inspection."}
              </p>
            </div>

            {activeTab === "home" ? (
              <DashboardHome
                dashboard={dashboard}
                focusModeEnabled={config.studyModeEnabled}
                onToggleFocusMode={handleToggleFocusMode}
                togglingFocusMode={togglingFocusMode}
              />
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
                      <p>Saving updates your user document in MongoDB and refreshes the stored source IP from this request.</p>
                    </div>
                    <button type="submit" disabled={saving}>
                      {saving ? "Saving..." : "Save settings"}
                    </button>
                  </div>
                ) : null}
              </>
            )}

            {error ? <div className="error-banner">{error}</div> : null}

            <div className="meta-strip">
              <div className="meta-box">
                <span>Registered IP</span>
                <strong>{user.registrationIp}</strong>
              </div>
              <div className="meta-box">
                <span>Last login IP</span>
                <strong>{user.lastLoginIp}</strong>
              </div>
              <div className="meta-box">
                <span>Last updated</span>
                <strong>{new Date(config.updatedAt).toLocaleString()}</strong>
              </div>
            </div>
          </section>
        </form>
      )}
    </main>
  );
}
