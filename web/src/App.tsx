import { FormEvent, useEffect, useState } from "react";
import { getCurrentUser, login, logout, register, saveConfig } from "./api";
import type { BlockCategory, FocusConfig, ScheduleWindow, User } from "./types";

const suggestionPrompts = [
  "I am a software engineer working on backend systems, APIs, debugging, and reading technical documentation. GitHub, docs, cloud dashboards, and Stack Overflow are usually on-topic.",
  "I am studying computer science and focusing on assignments, research, and interview preparation. Entertainment and social apps are usually off-topic during focus time.",
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

function makeScheduleWindow(): ScheduleWindow {
  return {
    id: crypto.randomUUID(),
    label: "Focus Block",
    days: [1, 2, 3, 4, 5],
    start: "09:00",
    end: "17:00"
  };
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
            Sign in to save your blocked categories, schedule windows, study mode,
            focus description, and source IP in MongoDB for later gateway use.
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

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [config, setConfig] = useState<FocusConfig | null>(null);
  const [authMode, setAuthMode] = useState<"login" | "register">("register");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [blacklistDraft, setBlacklistDraft] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const response = await getCurrentUser();
        if (response.user) {
          setUser(response.user);
          setConfig(response.user.focusConfig);
          setBlacklistDraft(response.user.focusConfig.blacklist.join(", "));
        }
      } catch {
        // ignore unauthenticated initial state
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  async function handleAuth(username: string, password: string) {
    setLoading(true);
    setError(null);
    try {
      const response =
        authMode === "login"
          ? await login(username, password)
          : await register(username, password);
      setUser(response.user);
      setConfig(response.user.focusConfig);
      setBlacklistDraft(response.user.focusConfig.blacklist.join(", "));
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
  }

  async function handleSave(event: FormEvent) {
    event.preventDefault();
    if (!config) {
      return;
    }

    setSaving(true);
    setError(null);
    try {
      const payload: FocusConfig = {
        ...config,
        blacklist: blacklistDraft
          .split(",")
          .map((entry) => entry.trim())
          .filter(Boolean)
      };
      const response = await saveConfig(payload);
      setConfig(response.config);
      setBlacklistDraft(response.config.blacklist.join(", "));
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

  return (
    <main className="app-shell">
      <section className="hero card">
        <div>
          <p className="eyebrow">Authenticated Setup</p>
          <h1>Configure focus filtering and persist it to MongoDB.</h1>
          <p className="lede">
            Your saved settings include schedule windows, categories, blacklist
            substrings, a focus summary for future LLM decisions, and your detected
            source IP address.
          </p>
        </div>
        <div className="hero-meta">
          <div className="meta-box">
            <span>Signed in as</span>
            <strong>{user.username}</strong>
          </div>
          <div className="meta-box">
            <span>Current source IP</span>
            <strong>{config.sourceIp || "Unknown"}</strong>
          </div>
          <button className="secondary-button" type="button" onClick={handleLogout}>
            Sign out
          </button>
        </div>
      </section>

      <form className="content-grid" onSubmit={handleSave}>
        <section className="card panel">
          <div className="panel-header">
            <h2>Focus Settings</h2>
            <p>Define when filtering is active and what types of browsing should be considered off-topic.</p>
          </div>

          <label className="toggle-row">
            <input
              type="checkbox"
              checked={config.studyModeEnabled}
              onChange={(event) =>
                setConfig({ ...config, studyModeEnabled: event.target.checked })
              }
            />
            <span>Enable study mode immediately</span>
          </label>

          <div className="schedule-block">
            <div className="schedule-header">
              <h3>Focus / Work Times</h3>
              <button
                type="button"
                className="secondary-button"
                onClick={() =>
                  setConfig({
                    ...config,
                    schedules: [...config.schedules, makeScheduleWindow()]
                  })
                }
              >
                Add time block
              </button>
            </div>

            {config.schedules.map((window) => (
              <div className="schedule-card" key={window.id}>
                <div className="schedule-top">
                  <input
                    value={window.label}
                    onChange={(event) =>
                      setConfig({
                        ...config,
                        schedules: config.schedules.map((entry) =>
                          entry.id === window.id
                            ? { ...entry, label: event.target.value }
                            : entry
                        )
                      })
                    }
                  />
                  <button
                    type="button"
                    className="danger-button"
                    onClick={() =>
                      setConfig({
                        ...config,
                        schedules: config.schedules.filter((entry) => entry.id !== window.id)
                      })
                    }
                  >
                    Remove
                  </button>
                </div>

                <div className="day-grid">
                  {dayLabels.map((day) => (
                    <label key={day.value} className="day-chip">
                      <input
                        type="checkbox"
                        checked={window.days.includes(day.value)}
                        onChange={(event) =>
                          setConfig({
                            ...config,
                            schedules: config.schedules.map((entry) => {
                              if (entry.id !== window.id) {
                                return entry;
                              }

                              return {
                                ...entry,
                                days: event.target.checked
                                  ? [...entry.days, day.value].sort()
                                  : entry.days.filter((value) => value !== day.value)
                              };
                            })
                          })
                        }
                      />
                      <span>{day.label}</span>
                    </label>
                  ))}
                </div>

                <div className="time-grid">
                  <label>
                    Start
                    <input
                      type="time"
                      value={window.start}
                      onChange={(event) =>
                        setConfig({
                          ...config,
                          schedules: config.schedules.map((entry) =>
                            entry.id === window.id
                              ? { ...entry, start: event.target.value }
                              : entry
                          )
                        })
                      }
                    />
                  </label>
                  <label>
                    End
                    <input
                      type="time"
                      value={window.end}
                      onChange={(event) =>
                        setConfig({
                          ...config,
                          schedules: config.schedules.map((entry) =>
                            entry.id === window.id
                              ? { ...entry, end: event.target.value }
                              : entry
                          )
                        })
                      }
                    />
                  </label>
                </div>
              </div>
            ))}
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

          <label>
            Site blacklist
            <textarea
              rows={3}
              value={blacklistDraft}
              onChange={(event) => setBlacklistDraft(event.target.value)}
              placeholder="tiktok, reddit, roblox"
            />
          </label>
        </section>

        <aside className="card panel side-panel">
          <div className="panel-header">
            <h2>Focus Description</h2>
            <p>Describe what productive work looks like so the future LLM layer has context.</p>
          </div>

          <label>
            What are you focusing on?
            <textarea
              rows={12}
              value={config.focusSummary}
              onChange={(event) => setConfig({ ...config, focusSummary: event.target.value })}
              placeholder="I am a student working on systems homework, reading papers, and building side projects..."
            />
          </label>

          <div className="suggestions">
            {suggestionPrompts.map((suggestion) => (
              <button
                key={suggestion}
                type="button"
                className="suggestion-chip"
                onClick={() =>
                  setConfig({
                    ...config,
                    focusSummary: config.focusSummary
                      ? `${config.focusSummary}\n\n${suggestion}`
                      : suggestion
                  })
                }
              >
                {suggestion}
              </button>
            ))}
          </div>

          <div className="prompt-box">
            <h3>Prompt Preview</h3>
            <pre>{promptPreview(config)}</pre>
          </div>
        </aside>

        <section className="card panel full-width">
          <div className="save-row">
            <div>
              <h2>Save Configuration</h2>
              <p>Saving updates your user document in MongoDB and refreshes the stored source IP from this request.</p>
            </div>
            <button type="submit" disabled={saving}>
              {saving ? "Saving..." : "Save settings"}
            </button>
          </div>

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
    </main>
  );
}
