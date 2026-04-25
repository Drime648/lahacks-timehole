# Gateway Context

## Purpose

The `gateway/` folder currently contains a Python DNS relay.

Current main file:

- `gateway/dns/dns.py`

## Current Behavior

The DNS relay:

- listens on UDP port `5354` by default
- parses incoming DNS requests
- extracts the queried domain
- gets the client source IP from the UDP packet address
- looks up the MongoDB user whose `focusConfig.sourceIp` matches that IP
- reads `focusConfig.blacklist`
- performs substring matching against the queried domain
- if matched:
  - returns a blackhole response
  - `0.0.0.0` for `A`
  - `::` for `AAAA`
- if not matched:
  - forwards the original DNS packet to `1.1.1.1:53`
  - returns the upstream response

## MongoDB Usage

The gateway reads:

- `MONGODB_URI`
- `MONGODB_DB_NAME`

Collection used:

- `users`

Lookup used:

```json
{ "focusConfig.sourceIp": "<client ip>" }
```

Field used for decisions:

```json
focusConfig.blacklist
```

## Cache

The relay has an in-memory cache keyed by:

- source IP
- query name

Each cache entry stores:

- `blocked` boolean
- expiration timestamp

Cache behavior:

- cache hit skips Mongo lookup and substring check
- cache miss fetches blacklist from Mongo and evaluates
- expired entries are removed lazily on read

TTL env var:

- `CACHE_TTL_SECONDS`

Default:

- `300`

## Current Limitations

- UDP only
- no TCP DNS support yet
- no schedule/study-mode evaluation yet
- no category lookup at the gateway level beyond the already-generated merged blacklist
- no active cache invalidation when user config changes
- exact source IP match only

## Useful Commands

From repo root:

```bash
python3 -m compileall gateway/dns
```

From `gateway/` with venv active:

```bash
pip install -r requirements.txt
python3 dns/dns.py
```

Useful env vars:

- `DNS_LISTEN_HOST`
- `DNS_PORT`
- `UPSTREAM_DNS_HOST`
- `UPSTREAM_DNS_PORT`
- `UPSTREAM_TIMEOUT_SECONDS`
- `MONGODB_URI`
- `MONGODB_DB_NAME`
- `CACHE_TTL_SECONDS`
