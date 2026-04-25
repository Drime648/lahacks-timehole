# Gateway DNS Relay

This is the first gateway component: a simple DNS relay written in Python.

Behavior:

- listens for UDP DNS queries on port `5354` by default
- inspects the requested domain name
- looks up the requesting client source IP in MongoDB via `focusConfig.sourceIp`
- loads that user's stored effective blacklist from `focusConfig.blacklist`
- blocks the query if any blacklist entry is a substring of the domain
- otherwise forwards the raw DNS packet to `1.1.1.1:53`
- returns the upstream response back to the client

Blocked domains are blackholed by returning `0.0.0.0` for `A` requests and `::` for `AAAA` requests.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r gateway/requirements.txt
python3 gateway/dns/main.py
```

The gateway automatically loads `gateway/.env`, so `MONGODB_URI` and `MONGODB_DB_NAME` do not need to be exported manually unless you want to override them for a shell session.

## Test

```bash
source gateway/.venv/bin/activate
pip install -r gateway/requirements.txt -r gateway/requirements-dev.txt
pytest gateway/tests
```
