import "dotenv/config";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import session from "express-session";
import MongoStore from "connect-mongo";
import { buildEffectiveBlacklist } from "./blacklists.js";
import { initDb, usersCollection } from "./db.js";
import { getRequestIp, hashPassword, normalizeUsername, verifyPassword } from "./auth.js";
const app = express();
const port = Number(process.env.WEB_API_PORT || 4000);
const sessionSecret = process.env.SESSION_SECRET || "dev-session-secret";
const defaultConfig = (sourceIp) => ({
    studyModeEnabled: false,
    schedules: [],
    blockedCategories: [],
    blacklist: [],
    manualBlacklist: [],
    categoryBlacklist: [],
    focusSummary: "",
    sourceIp,
    updatedAt: new Date().toISOString()
});
app.use(express.json());
app.use(session({
    secret: sessionSecret,
    resave: false,
    saveUninitialized: false,
    store: MongoStore.create({
        mongoUrl: process.env.MONGODB_URI,
        dbName: process.env.MONGODB_DB_NAME || "timehole",
        collectionName: "sessions"
    }),
    cookie: {
        httpOnly: true,
        sameSite: "lax",
        maxAge: 1000 * 60 * 60 * 24 * 14
    }
}));
function sanitizeUser(user) {
    return {
        username: user.username,
        createdAt: user.createdAt,
        updatedAt: user.updatedAt,
        registrationIp: user.registrationIp,
        lastLoginIp: user.lastLoginIp,
        focusConfig: {
            ...user.focusConfig,
            blacklist: user.focusConfig.manualBlacklist
        }
    };
}
async function requireUser(username) {
    if (!username) {
        return null;
    }
    return usersCollection().findOne({ username });
}
app.get("/api/health", (_request, response) => {
    response.json({ ok: true });
});
app.post("/api/auth/register", async (request, response) => {
    const username = normalizeUsername(String(request.body.username || ""));
    const password = String(request.body.password || "");
    if (!username || !password) {
        response.status(400).json({ error: "Username and password are required." });
        return;
    }
    const existing = await usersCollection().findOne({ username });
    if (existing) {
        response.status(409).json({ error: "That username is already taken." });
        return;
    }
    const sourceIp = getRequestIp(request);
    const now = new Date().toISOString();
    const user = {
        username,
        passwordHash: await hashPassword(password),
        createdAt: now,
        updatedAt: now,
        registrationIp: sourceIp,
        lastLoginIp: sourceIp,
        focusConfig: defaultConfig(sourceIp)
    };
    await usersCollection().insertOne(user);
    request.session.username = user.username;
    response.status(201).json({ user: sanitizeUser(user) });
});
app.post("/api/auth/login", async (request, response) => {
    const username = normalizeUsername(String(request.body.username || ""));
    const password = String(request.body.password || "");
    const user = await usersCollection().findOne({ username });
    if (!user || !(await verifyPassword(password, user.passwordHash))) {
        response.status(401).json({ error: "Invalid username or password." });
        return;
    }
    const sourceIp = getRequestIp(request);
    await usersCollection().updateOne({ username: user.username }, {
        $set: {
            lastLoginIp: sourceIp,
            "focusConfig.sourceIp": sourceIp,
            updatedAt: new Date().toISOString()
        }
    });
    request.session.username = user.username;
    const updatedUser = await usersCollection().findOne({ username: user.username });
    response.json({ user: sanitizeUser(updatedUser) });
});
app.post("/api/auth/logout", (request, response) => {
    request.session.destroy(() => {
        response.json({ ok: true });
    });
});
app.get("/api/auth/me", async (request, response) => {
    const user = await requireUser(request.session.username);
    if (!user) {
        response.status(401).json({ user: null });
        return;
    }
    response.json({ user: sanitizeUser(user) });
});
app.get("/api/config", async (request, response) => {
    const user = await requireUser(request.session.username);
    if (!user) {
        response.status(401).json({ error: "Not authenticated." });
        return;
    }
    response.json({
        config: {
            ...user.focusConfig,
            blacklist: user.focusConfig.manualBlacklist
        }
    });
});
app.put("/api/config", async (request, response) => {
    const user = await requireUser(request.session.username);
    if (!user) {
        response.status(401).json({ error: "Not authenticated." });
        return;
    }
    const sourceIp = getRequestIp(request);
    const blacklistParts = buildEffectiveBlacklist(Array.isArray(request.body.blockedCategories)
        ? request.body.blockedCategories
        : [], Array.isArray(request.body.blacklist) ? request.body.blacklist : []);
    const nextConfig = {
        studyModeEnabled: Boolean(request.body.studyModeEnabled),
        schedules: Array.isArray(request.body.schedules) ? request.body.schedules : [],
        blockedCategories: Array.isArray(request.body.blockedCategories)
            ? request.body.blockedCategories
            : [],
        blacklist: blacklistParts.effectiveBlacklist,
        manualBlacklist: blacklistParts.manualBlacklist,
        categoryBlacklist: blacklistParts.categoryBlacklist,
        focusSummary: String(request.body.focusSummary || ""),
        sourceIp,
        updatedAt: new Date().toISOString()
    };
    await usersCollection().updateOne({ username: user.username }, {
        $set: {
            focusConfig: nextConfig,
            updatedAt: nextConfig.updatedAt
        }
    });
    response.json({
        config: {
            ...nextConfig,
            blacklist: nextConfig.manualBlacklist
        }
    });
});
if (process.env.NODE_ENV === "production") {
    const __filename = fileURLToPath(import.meta.url);
    const __dirname = path.dirname(__filename);
    const clientDist = path.resolve(__dirname, "../dist");
    app.use(express.static(clientDist));
    app.get("*", (_request, response) => {
        response.sendFile(path.join(clientDist, "index.html"));
    });
}
initDb()
    .then(() => {
    app.listen(port, () => {
        console.log(`Web API listening on :${port}`);
    });
})
    .catch((error) => {
    console.error("Failed to initialize web API", error);
    process.exit(1);
});
