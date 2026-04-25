# Web Context

## Purpose

The `web/` folder contains a full-stack app:

- Vite + React frontend
- Express backend
- MongoDB-backed auth and config storage

## Key Files

- `web/server/index.ts`
  Main backend API and auth/session logic.

- `web/server/db.ts`
  MongoDB connection and user index setup.

- `web/server/auth.ts`
  Username normalization, password hashing, source IP extraction.

- `web/server/blacklists.ts`
  Loads category-specific blacklist files and builds effective blacklist output.

- `web/server/blacklists/*.txt`
  One file per category, line by line.

- `web/src/App.tsx`
  Main frontend app.

- `web/src/api.ts`
  Frontend API calls.

- `web/src/types.ts`
  Frontend shared shape for config/user.

## Auth Model

The app uses:

- username + password
- `express-session`
- `connect-mongo` session storage

The system used to be email-based earlier in development, but it was changed to username-based.

Important legacy fix already implemented:

- backend startup drops the old Mongo index `email_1` if it exists

This was needed because registration was failing with duplicate key errors on `email: null`.

## Frontend UX

### New users

After registration, the user goes through a 4-step onboarding wizard:

1. Study Schedule
2. Focus Prompt
3. Categories
4. Manual Blacklist

Each step has a next button and saves config to Mongo before advancing.

### Returning users

Returning users see the same sections as separate tabs instead of one giant page.

## Blacklist UX

The manual blacklist is line-by-line in the UI.

Example:

```text
tiktok
reddit
roblox
```

The frontend sends that as `blacklist`, but the backend stores:

- `manualBlacklist`
- `categoryBlacklist`
- merged `blacklist`

When returning config to the frontend, the backend maps `blacklist` back to the manual values for editing.

## Source IP Behavior

The backend stores source IP using:

- `x-forwarded-for` if present
- otherwise `request.socket.remoteAddress`

It updates:

- `registrationIp`
- `lastLoginIp`
- `focusConfig.sourceIp`

The gateway later uses `focusConfig.sourceIp` as the lookup key.

## Useful Commands

From `web/`:

Install:

```bash
npm install
```

Run backend:

```bash
npm run dev:server
```

Run frontend:

```bash
npm run dev:client
```

Typecheck:

```bash
npm run typecheck
```

Build:

```bash
npm run build
```

Frontend dev port:

- `http://localhost:3000`

Backend API port:

- `http://localhost:4000`

The Vite dev server proxies `/api` to the backend automatically.
