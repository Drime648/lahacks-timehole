# Project Context

## Goal

TimeHole is a focus-time filtering system with two main pieces:

- `web/`: full-stack web app for user auth and configuration
- `gateway/`: Python DNS relay that uses MongoDB-stored user settings

The long-term idea is:

- users create an account in the web app
- users configure study/work schedules, focus goals, blocked categories, and a manual blacklist
- the web app stores all of that in MongoDB
- the gateway looks up the requesting client's source IP in MongoDB
- the gateway uses the stored blacklist/settings to decide whether to blackhole a DNS query

## Current MongoDB Connection

The current web app `.env` uses:

`mongodb+srv://lahacks_demo:cm1a4mqY1aJxyjQW@cluster0.41twxft.mongodb.net/?appName=Cluster0`

That value is stored in:

- `web/.env`

## Current High-Level Status

Implemented:

- username/password auth in the web app
- MongoDB-backed user documents
- onboarding wizard for new users
- tabbed settings UI for returning users
- category-specific blacklist files on the backend
- effective blacklist generation in the web backend
- Python DNS relay that:
  - listens for UDP DNS requests
  - looks up the user by `focusConfig.sourceIp`
  - reads `focusConfig.blacklist`
  - performs substring matching
  - blackholes blocked domains
  - forwards allowed domains to `1.1.1.1`
  - caches allow/block decisions per source IP and query name with TTL

Not implemented yet:

- HTTP/HTTPS proxy integration
- LLM-based decisioning in the gateway
- gateway use of schedules/study mode/focus prompt beyond blacklist matching
- cache invalidation tied to config changes
- production hardening/auth hardening/tests

## Main User Data Shape

Each user document in MongoDB contains:

- `username`
- `passwordHash`
- `createdAt`
- `updatedAt`
- `registrationIp`
- `lastLoginIp`
- `focusConfig`

`focusConfig` currently contains:

- `studyModeEnabled`
- `schedules`
- `blockedCategories`
- `blacklist`
- `manualBlacklist`
- `categoryBlacklist`
- `focusSummary`
- `sourceIp`
- `updatedAt`

Important distinction:

- `manualBlacklist`: user-entered blacklist
- `categoryBlacklist`: generated from selected categories
- `blacklist`: effective merged blacklist actually used by the gateway

## Important Behavior Notes

- The web UI edits the manual blacklist only.
- The backend merges manual blacklist + category blacklist when saving config.
- The DNS relay currently keys user lookup by exact `focusConfig.sourceIp`.
- If no user is found for a source IP, the DNS relay allows the request.
- The DNS cache is in-memory only and uses TTL expiration.
