# Gateway DNS Relay

This is the first gateway component: a simple DNS relay written in Python.

Behavior:

- listens for UDP DNS queries on port `5354` by default
- inspects the requested domain name
- blocks the query if any hardcoded blacklist entry is a substring of the domain
- otherwise forwards the raw DNS packet to `1.1.1.1:53`
- returns the upstream response back to the client

Blocked domains are blackholed by returning `0.0.0.0` for `A` requests and `::` for `AAAA` requests.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r gateway/requirements.txt
python3 gateway/src/main.py
```
