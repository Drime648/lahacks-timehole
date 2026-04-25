# Gateway

This gateway runs both the DNS relay and the web proxy in one Python process, with separate listener ports for each protocol.

Behavior:

- listens for UDP DNS queries on port `53` by default when started through `gateway/main.py`
- listens for web proxy traffic on port `8080` by default
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
python3 gateway/main.py
```

The gateway automatically loads `gateway/.env`, so `MONGODB_URI` and `MONGODB_DB_NAME` do not need to be exported manually unless you want to override them for a shell session.

Notes:

- Binding UDP port `53` usually requires elevated privileges on macOS/Linux. If you do not want to run with elevated privileges, override it:

```bash
DNS_PORT=5354 python3 gateway/main.py
```

- You can still run each service individually with:

```bash
python3 gateway/dns/main.py
python3 gateway/proxy/main.py
```

## Test

```bash
source gateway/.venv/bin/activate
pip install -r gateway/requirements.txt -r gateway/requirements-dev.txt
pytest gateway/tests
```
