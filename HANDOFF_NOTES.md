# Handoff Notes

## If starting a new chat

Tell the new chat to read:

1. `PROJECT_CONTEXT.md`
2. `WEB_CONTEXT.md`
3. `GATEWAY_CONTEXT.md`
4. `HANDOFF_NOTES.md`

That should be enough to recover the current state quickly.

## Most Important Current Facts

- The web app is farther along than the gateway.
- The web app already stores the effective blacklist in Mongo.
- The DNS relay already uses Mongo and source IP lookup.
- The cache now has TTL.

## Recommended Next Tasks

Good next steps:

1. Add TCP DNS support to the gateway.
2. Use schedule/study-mode logic in the gateway instead of only blacklist matching.
3. Add tests for:
   - web auth
   - config save/merge behavior
   - category blacklist generation
   - DNS gateway allow/block decisions
4. Add a way to invalidate or refresh gateway cache when user settings change.
5. Decide whether `sourceIp` should represent:
   - the current client IP only
   - multiple known IPs per user
   - a device-level registration model

## Important Caveats

- `.env` files and `.venv` directories should stay ignored by git.
- The current Mongo credentials live in `web/.env`.
- The gateway does not automatically load `.env`; it currently relies on env vars being present in the process environment.
- The web backend does load `.env` automatically via `dotenv/config`.

## Git / Repo Notes

- Branch currently used recently: `main`
- Recent commits included:
  - onboarding/tabbed web UI
  - category-generated blacklist support
  - gateway Mongo-backed blacklist lookup
  - gateway cache TTL

## What to explain to a new chat in one sentence

"This repo has a full-stack `web` app that stores per-user focus settings in Mongo and a Python `gateway` DNS relay that looks users up by source IP and blocks domains using the stored effective blacklist, with an in-memory TTL cache."
