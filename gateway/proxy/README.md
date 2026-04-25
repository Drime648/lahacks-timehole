# Gateway Web Proxy

This proxy lives in `gateway/proxy` and is meant to handle layer 7 inspection for web traffic.

Current behavior:

- accepts standard browser proxy traffic on an HTTP proxy port
- inspects plain HTTP requests using the full URL:
  - host
  - URL path
  - query string
- applies a hardcoded URL-path blacklist
- only filters when the user's focus mode is active
- supports HTTPS `CONNECT`
- performs HTTPS MITM interception using a locally generated TimeHole root CA
- can inspect encrypted HTTPS request URLs once the CA is trusted in the browser
- exposes the root CA download at `/__timehole/ca.crt`

## Run

```bash
source gateway/.venv/bin/activate
pip install -r gateway/requirements.txt -r gateway/requirements-dev.txt
export MONGODB_URI="mongodb+srv://..."
export MONGODB_DB_NAME="timehole"
python3 gateway/proxy/main.py
```

## HTTPS inspection model

HTTPS inspection works through TLS interception (MITM):

1. Generate a local root CA certificate for the proxy.
2. Trust that CA in the browser or OS trust store.
3. When the browser sends `CONNECT`, the proxy terminates TLS locally instead of blindly tunneling.
4. The proxy generates or serves a certificate for the target host signed by the local CA.
5. The browser creates a TLS session to the proxy, trusting it because the CA is installed.
6. The proxy opens a second TLS session to the real upstream website.
7. The proxy can then inspect request URLs, headers, and responses before forwarding them.

This repo now implements that model for request URL inspection. Response forwarding is in place too, so response-body inspection can be added later in the same MITM path.

## Browser setup

To use this as a forward proxy in a browser today:

1. Start the proxy on the machine running `gateway/proxy/main.py`.
2. Download the root CA from `http://127.0.0.1:8080/__timehole/ca.crt` or the matching gateway host.
3. Import that certificate into your browser or OS trust store.
4. In your browser or OS proxy settings, set:
   - HTTP proxy: `127.0.0.1:8080`
   - HTTPS proxy: `127.0.0.1:8080`
5. Browse normally.

Notes:

- HSTS and certificate pinning can break some sites/apps under HTTPS interception.
- It is best to test with a dedicated browser profile while building this.
