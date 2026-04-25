import type { DnsDashboard, FocusConfig, User } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed with status ${response.status}`);
  }

  return data as T;
}

export function getCurrentUser(): Promise<{ user: User | null }> {
  return request("/api/auth/me");
}

export function register(username: string, password: string): Promise<{ user: User }> {
  return request("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
}

export function login(username: string, password: string): Promise<{ user: User }> {
  return request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password })
  });
}

export function logout(): Promise<{ ok: true }> {
  return request("/api/auth/logout", { method: "POST" });
}

export function saveConfig(config: FocusConfig): Promise<{ config: FocusConfig }> {
  return request("/api/config", {
    method: "PUT",
    body: JSON.stringify(config)
  });
}

export function getDnsDashboard(): Promise<DnsDashboard> {
  return request("/api/dns-dashboard");
}
