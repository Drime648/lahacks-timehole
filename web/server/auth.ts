import bcrypt from "bcryptjs";
import type { Request } from "express";

export function normalizeUsername(username: string): string {
  return username.trim().toLowerCase();
}

export async function hashPassword(password: string): Promise<string> {
  return bcrypt.hash(password, 12);
}

export async function verifyPassword(
  password: string,
  passwordHash: string
): Promise<boolean> {
  return bcrypt.compare(password, passwordHash);
}

export function getRequestIp(request: Request): string {
  const forwardedFor = request.headers["x-forwarded-for"];
  const raw = Array.isArray(forwardedFor)
    ? forwardedFor[0]
    : forwardedFor?.split(",")[0] || request.socket.remoteAddress || "";

  return raw.replace(/^::ffff:/, "").trim();
}
