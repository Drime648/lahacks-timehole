import bcrypt from "bcryptjs";
export function normalizeUsername(username) {
    return username.trim().toLowerCase();
}
export async function hashPassword(password) {
    return bcrypt.hash(password, 12);
}
export async function verifyPassword(password, passwordHash) {
    return bcrypt.compare(password, passwordHash);
}
export function getRequestIp(request) {
    const forwardedFor = request.headers["x-forwarded-for"];
    const raw = Array.isArray(forwardedFor)
        ? forwardedFor[0]
        : forwardedFor?.split(",")[0] || request.socket.remoteAddress || "";
    return raw.replace(/^::ffff:/, "").trim();
}
